import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { MAX_UPLOAD_BYTES, decodeUploadBase64 } from '../../../server/src/api/uploads.ts';
import { PlaybackLinks, confinedFile, safePublicRelative } from '../../../server/src/publisher/playback.ts';
import { exportDirectory } from '../../../server/src/publisher/open-directory.ts';
import { createProject } from '../../../server/src/store/workspace.ts';
import { EditService } from '../../../server/src/edit/service.ts';

let root: string;
beforeEach(() => { root = mkdtempSync(path.join(os.tmpdir(), 'playback-safety-')); });
afterEach(() => { rmSync(root, { recursive: true, force: true }); });

describe('播放范围与上传输入限制', () => {
  it('拒绝非法及过大的Base64', () => {
    for (const value of ['AA=A', 'A===', 'Zh==', '', 'data:image/png;base64,AAAA', ' AAAA ']) expect(() => decodeUploadBase64(value)).toThrow();
    expect(() => decodeUploadBase64(Buffer.alloc(MAX_UPLOAD_BYTES + 1).toString('base64'))).toThrow('不能超过');
    expect(decodeUploadBase64('Zg==').toString()).toBe('f');
  });
  it('限制相对路径和目录穿越，拒绝符号链接', () => {
    for (const relative of ['../project.json', '.studio/state.json', 'levels/../project.json', '/project.json', 'levels\\x.html', 'levels/C:x.html']) expect(safePublicRelative(relative)).toBe(false);
    const outside = path.join(root, 'outside');
    const published = path.join(root, 'published');
    mkdirSync(outside); mkdirSync(published);
    writeFileSync(path.join(outside, 'secret.json'), '{}');
    symlinkSync(outside, path.join(published, 'levels'), process.platform === 'win32' ? 'junction' : 'dir');
    expect(confinedFile(published, 'levels/secret.json')).toBeNull();
    expect(() => exportDirectory(root, 'prj_valid0001', '../outside')).toThrow();
  });
  it('票据在有效期外失效，随机无效票据不被接受', () => {
    const project = createProject(root, { title: '过期播放' });
    const edit = new EditService({ workspaceRoot: root, importRoots: [] });
    const playerRoot = path.join(root, 'app', 'dist-player');
    mkdirSync(playerRoot, { recursive: true });
    writeFileSync(path.join(playerRoot, 'player.html'), '<html></html>');
    let now = 1000;
    const playback = new PlaybackLinks({ workspaceRoot: root, repoRoot: path.join(root, 'app'), now: () => now, ttlMs: 10 });
    const link = playback.project('http://127.0.0.1:1234', project.projectId, edit.snapshot(project.projectId));
    now = 1010;
    expect(() => playback.serve({ method: 'GET' } as IncomingMessage, {} as ServerResponse, new URL(link.url).pathname)).toThrow('已过期');
    expect(() => playback.serve({ method: 'GET' } as IncomingMessage, {} as ServerResponse, `/v1/playback/${'0'.repeat(64)}/player.html`)).toThrow('已过期');
  });
});
