import { afterEach, describe, expect, it } from 'vitest';
import { spawn } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { REPO_ROOT } from '../helpers/harness.ts';

const directories: string[] = [];
function run(launcher: string, args: string[] = []): Promise<{ code: number | null; output: string }> {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [launcher, ...args], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '';
    child.stdout.on('data', (chunk: Buffer) => { output += chunk.toString(); });
    child.stderr.on('data', (chunk: Buffer) => { output += chunk.toString(); });
    child.once('error', reject);
    child.once('close', (code) => resolve({ code, output }));
  });
}
function packageFixture(kind: 'editor' | 'player'): string {
  const root = mkdtempSync(path.join(os.tmpdir(), 'studio-discovery-'));
  directories.push(root);
  cpSync(path.join(REPO_ROOT, 'scripts/studio-portable-launch.mjs'), path.join(root, 'launch.mjs'));
  writeFileSync(path.join(root, 'portable.json'), JSON.stringify({ kind, platform: 'win-x64' }));
  if (kind === 'editor') {
    cpSync(path.join(REPO_ROOT, 'server'), path.join(root, 'app/server'), { recursive: true });
    cpSync(path.join(REPO_ROOT, 'src/runtime'), path.join(root, 'app/src/runtime'), { recursive: true });
    mkdirSync(path.join(root, 'app/dist'), { recursive: true });
    writeFileSync(path.join(root, 'app/dist/studio-live.html'), '<html>隔离编辑器恢复测试</html>');
    writeFileSync(path.join(root, 'app/package.json'), JSON.stringify({ type: 'module' }));
  } else writeFileSync(path.join(root, 'player.html'), '<html>隔离展示器恢复测试</html>');
  mkdirSync(path.join(root, 'data'));
  // Every fixture gets its own static port and workspace, independent of a running demo.
  writeFileSync(path.join(root, 'data/preferences.json'), JSON.stringify({ port: 0 }));
  return root;
}
type LauncherState = { pid: number; origin: string; url: string; secret: string };
function state(root: string): LauncherState { return JSON.parse(readFileSync(path.join(root, 'data/launcher-state.json'), 'utf8')); }
afterEach(async () => {
  for (const root of directories.splice(0)) {
    await run(path.join(root, 'launch.mjs'), ['stop']);
    if (existsSync(root)) rmSync(root, { recursive: true, force: true });
  }
});

describe('便携编辑器重启发现', () => {
  it('重启后同源发现新后台端口，作品和旧修订仍保留，接口不泄露凭据', async () => {
    const root = packageFixture('editor');
    const launcher = path.join(root, 'launch.mjs');
    const firstStart = await run(launcher, ['--no-browser']);
    expect(firstStart.code, firstStart.output).toBe(0);
    const first = state(root);
    const discovery = await fetch(`${first.origin}/__studio/discovery`, { headers: { Origin: first.origin, 'Sec-Fetch-Site': 'same-origin' } });
    expect(discovery.status).toBe(200);
    expect(discovery.headers.get('cache-control')).toBe('no-store');
    expect(discovery.headers.has('access-control-allow-origin')).toBe(false);
    const previous = await discovery.json() as { apiUrl: string };
    expect(Object.keys(previous)).toEqual(['apiUrl']);
    expect(previous.apiUrl).toBe(new URL(first.url).searchParams.get('api'));
    expect((await fetch(`${first.origin}/__studio/discovery`, { headers: { Origin: 'http://localhost:1234' } })).status).toBe(403);
    expect((await fetch(`${first.origin}/__studio/discovery`, { headers: { 'Sec-Fetch-Site': 'cross-site' } })).status).toBe(403);
    expect((await fetch(`${first.origin}/__portable/status`)).status).toBe(403);
    expect((await fetch(`${first.origin}/__portable/status`, { headers: { 'x-portable-key': first.secret } })).status).toBe(200);
    const ui = await fetch(`${previous.apiUrl}/v1/ui-session`, { method: 'POST', headers: { Origin: first.origin, 'content-type': 'application/json' } }).then((response) => response.json()) as { token: string };
    const project = await fetch(`${previous.apiUrl}/v1/projects`, { method: 'POST', headers: { authorization: `Bearer ${ui.token}`, 'content-type': 'application/json' }, body: JSON.stringify({ title: '重启后保留的原创测试作品' }) }).then((response) => response.json()) as { projectId: string; revision: string };
    const stopped = await run(launcher, ['stop']);
    expect(stopped.code, stopped.output).toBe(0);
    const occupiedApi = http.createServer((_req, res) => res.end('old port occupied'));
    await new Promise<void>((resolve, reject) => { occupiedApi.once('error', reject); occupiedApi.listen(Number(new URL(previous.apiUrl).port), '127.0.0.1', resolve); });
    try {
      const restarted = await run(launcher, ['--no-browser']);
      expect(restarted.code, restarted.output).toBe(0);
      const second = state(root);
      expect(second.origin).toBe(first.origin);
      const rediscovered = await fetch(`${first.origin}/__studio/discovery`).then((response) => response.json()) as { apiUrl: string };
      expect(rediscovered.apiUrl).not.toBe(previous.apiUrl);
      expect(rediscovered.apiUrl).toBe(new URL(second.url).searchParams.get('api'));
      const reconnect = await fetch(`${rediscovered.apiUrl}/v1/ui-session`, { method: 'POST', headers: { Origin: first.origin, 'content-type': 'application/json' } });
      expect(reconnect.status).toBe(201);
      const session = await reconnect.json() as { token: string };
      const projectResponse = await fetch(`${rediscovered.apiUrl}/v1/projects/${project.projectId}`, { headers: { authorization: `Bearer ${session.token}` } });
      expect(projectResponse.status).toBe(200);
      expect(await projectResponse.json()).toMatchObject({ revision: project.revision, project: { title: '重启后保留的原创测试作品' } });
    } finally { await new Promise<void>((resolve) => occupiedApi.close(() => resolve())); }
  }, 60000);

  it('展示器不提供编辑器后台发现接口', async () => {
    const root = packageFixture('player');
    const started = await run(path.join(root, 'launch.mjs'), ['--no-browser']);
    expect(started.code, started.output).toBe(0);
    expect((await fetch(`${state(root).origin}/__studio/discovery`)).status).toBe(404);
  });
});
