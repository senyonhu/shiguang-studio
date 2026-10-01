import { describe, expect, it } from 'vitest';
import { spawn } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PLAYER_SERVE_SCRIPT } from '../../../server/src/publisher/player-server.ts';
import { EditService } from '../../../server/src/edit/service.ts';
import type { ActorRef } from '../../../server/src/edit/service.ts';
import { createProject } from '../../../server/src/store/workspace.ts';

describe('publisher audit regressions', () => {
  it('legacy portable helper confines an explicit public root and supports safe GET/HEAD only', async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'studio-pack-server-audit-'));
    const published = path.join(root, 'public');
    const sibling = path.join(root, 'public-private');
    mkdirSync(path.join(published, '.hidden'), { recursive: true });
    mkdirSync(path.join(published, 'normal'), { recursive: true });
    mkdirSync(sibling);
    writeFileSync(path.join(published, 'index.html'), '<html>public fixture</html>');
    writeFileSync(path.join(published, 'normal/file.txt'), 'public payload');
    writeFileSync(path.join(published, '.hidden/secret.txt'), 'hidden fixture');
    writeFileSync(path.join(sibling, 'secret.txt'), 'sibling private fixture');
    symlinkSync(sibling, path.join(published, 'outside'), process.platform === 'win32' ? 'junction' : 'dir');
    symlinkSync(path.join(published, 'normal'), path.join(published, 'alias'), process.platform === 'win32' ? 'junction' : 'dir');
    const script = fileURLToPath(new URL('../../../scripts/studio-pack-serve.mjs', import.meta.url));
    const child = spawn(process.execPath, [script, '--root', published, '--port', '0'], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    try {
      const origin = await new Promise<string>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Portable helper startup timeout')), 10000);
        let output = '';
        child.stdout.on('data', (chunk) => { output += String(chunk); const match = /http:\/\/127\.0\.0\.1:\d+/.exec(output); if (match) { clearTimeout(timer); resolve(match[0]); } });
        child.once('exit', (code) => { clearTimeout(timer); reject(new Error(`Portable helper exited ${code}`)); });
      });
      expect((await fetch(`${origin}/`)).status).toBe(200);
      for (const relative of ['%2e%2e%2fpublic-private/secret.txt', '.hidden/secret.txt', 'outside/secret.txt', 'alias/file.txt', 'normal%5c..%5csecret.txt']) {
        expect((await fetch(`${origin}/${relative}`)).status, relative).toBe(403);
      }
      expect((await fetch(`${origin}/%E0%A4%A`)).status).toBe(400);
      const head = await fetch(`${origin}/normal/file.txt`, { method: 'HEAD', headers: { Origin: 'https://unrelated.example' } });
      expect(head.status).toBe(200);
      expect(head.headers.get('content-length')).toBe(String(Buffer.byteLength('public payload')));
      expect(await head.text()).toBe('');
      expect(head.headers.get('access-control-allow-origin')).toBeNull();
      const disallowed = await fetch(`${origin}/normal/file.txt`, { method: 'POST' });
      expect(disallowed.status).toBe(405);
      expect(disallowed.headers.get('allow')).toBe('GET, HEAD');
      const surviving = await fetch(`${origin}/normal/file.txt`);
      expect(surviving.status).toBe(200);
      expect(await surviving.text()).toBe('public payload');
    } finally {
      if (child.exitCode === null) await new Promise<void>((resolve) => { child.once('exit', () => resolve()); child.kill(); });
      if (path.dirname(path.resolve(root)) !== path.resolve(os.tmpdir()) || !path.basename(root).startsWith('studio-pack-server-audit-')) throw new Error('Unsafe cleanup target');
      rmSync(root, { recursive: true, force: true });
    }
  });
  it('standalone fallback serves only content, confines paths and survives malformed URLs', async () => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'studio-player-server-audit-'));
    const published = path.join(root, 'published');
    mkdirSync(path.join(published, 'content/levels'), { recursive: true });
    mkdirSync(path.join(published, 'data')); mkdirSync(path.join(published, 'runtime'));
    writeFileSync(path.join(published, 'serve.mjs'), PLAYER_SERVE_SCRIPT);
    writeFileSync(path.join(published, 'player.html'), '<html>player</html>');
    writeFileSync(path.join(published, 'content/content.json'), '0123456789');
    writeFileSync(path.join(published, 'data/launcher-state.json'), '{"secret":"fixture-only"}');
    writeFileSync(path.join(published, 'runtime/node.exe'), 'fixture');
    writeFileSync(path.join(root, 'private.txt'), 'private fixture');
    symlinkSync(root, path.join(published, 'content/levels/outside'), process.platform === 'win32' ? 'junction' : 'dir');
    const child = spawn(process.execPath, [path.join(published, 'serve.mjs'), '--port', '0'], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    try {
      const origin = await new Promise<string>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Player server startup timeout')), 10000);
        let output = '';
        child.stdout.on('data', (chunk) => { output += String(chunk); const match = /http:\/\/127\.0\.0\.1:\d+/.exec(output); if (match) { clearTimeout(timer); resolve(match[0]); } });
        child.once('exit', (code) => { clearTimeout(timer); reject(new Error(`Player server exited ${code}`)); });
      });
      expect((await fetch(`${origin}/player.html`)).status).toBe(200);
      for (const relative of ['data/launcher-state.json', 'runtime/node.exe', 'serve.mjs', 'content/levels/outside/private.txt', 'content/%2e%2e%2fdata/launcher-state.json']) expect((await fetch(`${origin}/${relative}`)).status, relative).toBe(403);
      expect((await fetch(`${origin}/content/%E0%A4%A`)).status).toBe(400);
      const response = await fetch(`${origin}/content/content.json`, { headers: { range: 'bytes=2-5', Origin: 'https://unrelated.example' } });
      expect(response.status).toBe(206); expect(await response.text()).toBe('2345');
      expect(response.headers.get('access-control-allow-origin')).toBeNull();
      expect((await fetch(`${origin}/content/content.json`, { method: 'HEAD' })).headers.get('content-length')).toBe('10');
      expect((await fetch(`${origin}/content/content.json`, { method: 'POST' })).status).toBe(405);
      expect((await fetch(`${origin}/content/content.json`, { headers: { range: 'bytes=999999999999999999999-' } })).status).toBe(416);
    } finally {
      if (child.exitCode === null) await new Promise<void>((resolve) => { child.once('exit', () => resolve()); child.kill(); });
      if (path.dirname(path.resolve(root)) !== path.resolve(os.tmpdir()) || !path.basename(root).startsWith('studio-player-server-audit-')) throw new Error('Unsafe cleanup target');
      rmSync(root, { recursive: true, force: true });
    }
  });
  it('undoing a scene deletion restores its original position', () => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'studio-scene-order-audit-'));
    try {
      const project = createProject(root, { title: 'scene order' });
      const service = new EditService({ workspaceRoot: root, importRoots: [] });
      const actor: ActorRef = { actorId: 'audit', label: 'audit', sessionId: 'sess_audit0001', channel: 'mcp', scopes: ['read', 'edit'] };
      const added = service.applyTransaction(actor, { projectId: project.projectId, baseRevision: '0', requestId: 'create-scenes', operations: ['sc_middle0001', 'sc_final00001'].map((sceneId) => ({ action: 'scene.create', payload: { chapterId: project.chapterId, sceneId, title: sceneId } })) });
      expect(added.status).toBe('applied');
      const original = [...service.snapshot(project.projectId).content.chapters[0].sceneIds];
      const removed = service.applyTransaction(actor, { projectId: project.projectId, baseRevision: added.revision, requestId: 'delete-middle', operations: [{ action: 'scene.delete', payload: { sceneId: 'sc_middle0001' } }] });
      expect(removed.status).toBe('applied');
      const restored = service.undo(actor, { projectId: project.projectId, baseRevision: removed.revision, requestId: 'undo-middle', undoId: removed.undoId! });
      expect(restored.status).toBe('applied');
      expect(service.snapshot(project.projectId).content.chapters[0].sceneIds).toEqual(original);
    } finally { rmSync(root, { recursive: true, force: true }); }
  });
});
