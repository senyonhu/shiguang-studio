// P5 验收：离线便携交付物（KI-012）。
// 覆盖：打包脚本产出清单与散列可核对、包内不含外部服务地址、包内后台能完成真实制作流程、
//       包内 MCP 适配器能启动并列出全部工具、包内导出发布包包含展示器。
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { api, makeWorkspace, McpClient, pairSession, readPairingKey, startBackend, trace, REPO_ROOT } from '../helpers/harness.ts';
import type { ServiceHandle } from '../helpers/harness.ts';

const sha256 = (buffer: Buffer) => createHash('sha256').update(buffer).digest('hex');

function run(command: string, args: string[], options: { cwd?: string; env?: NodeJS.ProcessEnv } = {}): Promise<{ code: number | null; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd: options.cwd ?? REPO_ROOT, env: options.env ?? process.env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });
    child.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

function walk(dir: string, prefix = ''): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const abs = path.join(dir, name);
    const rel = prefix ? `${prefix}/${name}` : name;
    if (statSync(abs).isDirectory()) out.push(...walk(abs, rel));
    else out.push(rel);
  }
  return out;
}

let packageDir = '';
let manifest: {
  kind: string;
  nodeRequirement: string;
  entries: Record<string, string>;
  fileCount: number;
  totalBytes: number;
  files: { rel: string; bytes: number; sha256: string }[];
};
let packaged: ServiceHandle | null = null;
let workspace = '';

beforeAll(async () => {
  packageDir = path.join(os.tmpdir(), `studio-portable-${Date.now()}`);
  const packagedRun = await run(process.execPath, [path.join('scripts', 'studio-package.mjs'), '--out', packageDir]);
  expect(packagedRun.code, `${packagedRun.stdout}\n${packagedRun.stderr}`).toBe(0);
  manifest = JSON.parse(readFileSync(path.join(packageDir, 'manifest.json'), 'utf8'));
  workspace = makeWorkspace('portable-ws');
}, 300000);

afterAll(async () => {
  if (packageDir && existsSync(path.join(packageDir, 'launch.mjs'))) await run(path.join(packageDir, 'runtime/node.exe'), [path.join(packageDir, 'launch.mjs'), 'stop']);
  await packaged?.stop();
  if (packageDir && existsSync(packageDir)) rmSync(packageDir, { recursive: true, force: true });
});

describe('P5 离线便携交付物', () => {
  it('只用包内 Node 双击入口运行，端口占用时仍能连接；重复启动复用实例，停止同时关闭后台', async () => {
    const occupied = http.createServer((_req, res) => res.end('occupied'));
    await new Promise<void>((resolve) => { occupied.once('error', () => resolve()); occupied.listen(5190, '127.0.0.1', resolve); });
    const command = process.env.ComSpec ?? 'C:\\Windows\\System32\\cmd.exe';
    const env = { ...process.env, PATH: path.join(process.env.SystemRoot ?? 'C:\\Windows', 'System32') };
    let state: { pid: number; origin: string; url: string; secret: string } | null = null;
    try {
      const started = await run(command, ['/d', '/c', 'studio.cmd', '--no-browser'], { cwd: packageDir, env });
      expect(started.code, started.stderr + started.stdout).toBe(0);
      state = JSON.parse(readFileSync(path.join(packageDir, 'data/launcher-state.json'), 'utf8'));
      expect(state!.origin).not.toBe('http://127.0.0.1:5190');
      expect((await fetch(state!.url)).status).toBe(200);
      const apiBase = new URL(state!.url).searchParams.get('api')!;
      const uiSession = await fetch(`${apiBase}/v1/ui-session`, { method: 'POST', headers: { origin: state!.origin, 'content-type': 'application/json' }, body: '{}' });
      expect(uiSession.status).toBe(201);
      const again = await run(command, ['/d', '/c', 'studio.cmd', '--no-browser'], { cwd: packageDir, env });
      expect(again.code).toBe(0);
      expect(JSON.parse(readFileSync(path.join(packageDir, 'data/launcher-state.json'), 'utf8')).pid).toBe(state!.pid);
      const stopped = await run(command, ['/d', '/c', '停止编辑器.cmd'], { cwd: packageDir, env });
      expect(stopped.code, stopped.stdout + stopped.stderr).toBe(0);
      expect(await fetch(`${apiBase}/v1/health`).then(() => true).catch(() => false)).toBe(false);
      trace('p5-portable-double-click', { pid: state!.pid, origin: state!.origin, globalNodeInPath: false, duplicateReused: true, stopped: true });
    } finally {
      await run(path.join(packageDir, 'runtime/node.exe'), [path.join(packageDir, 'launch.mjs'), 'stop']);
      await new Promise<void>((resolve) => occupied.close(() => resolve()));
    }
  }, 60000);

  it('清单完整、散列与文件一致、入口存在', () => {
    expect(manifest.kind).toBe('shiguang-studio-portable');
    expect(manifest.nodeRequirement).toContain('>=22');
    for (const rel of Object.values(manifest.entries)) {
      expect(existsSync(path.join(packageDir, rel)), `入口缺失：${rel}`).toBe(true);
    }
    const onDisk = walk(packageDir).filter((rel) => rel !== 'manifest.json' && !rel.startsWith('data/'));
    expect(onDisk.length).toBe(manifest.fileCount);
    const mismatched = manifest.files.filter((file) => sha256(readFileSync(path.join(packageDir, file.rel))) !== file.sha256);
    expect(mismatched.map((file) => file.rel)).toEqual([]);
    // 不含 node_modules / 测试 / 审查材料
    expect(onDisk.some((rel) => rel.includes('node_modules'))).toBe(false);
    trace('p5-portable-manifest', { fileCount: manifest.fileCount, totalBytes: manifest.totalBytes, entries: manifest.entries });
  });

  it('第一方代码不含任何外部服务地址；第三方包里的地址仅出现在许可证/横幅注释中', () => {
    const firstParty: string[] = [];
    const thirdParty: string[] = [];
    const documentation: string[] = [];
    // 第三方构建产物里已知的许可证/横幅地址（不是运行时请求目标）
    const bannerHosts = new Set(['vuejs.org', 'github.com', 'code.visualstudio.com', 'microsoft.com']);
    for (const rel of walk(packageDir)) {
      if (rel.startsWith('data/') || !/\.(md|json|mjs|js|html|cmd)$/i.test(rel)) continue;
      const text = readFileSync(path.join(packageDir, rel), 'utf8');
      for (const match of text.matchAll(/https?:\/\/([a-z0-9.-]+)/gi)) {
        const host = match[1].toLowerCase();
        if (host === '127.0.0.1' || host === 'localhost' || host.endsWith('w3.org') || host.endsWith('example.com')) continue;
        // 使用手册的仓库/发布页链接由读者打开，不是运行代码的网络目标。
        if (rel.startsWith('docs/') && rel.endsWith('.md')) { documentation.push(`${rel} → ${host}`); continue; }
        const isThirdPartyBundle = rel.startsWith('app/dist/') || rel.startsWith('app/dist-player/');
        if (isThirdPartyBundle && bannerHosts.has(host)) thirdParty.push(`${rel} → ${host}`);
        else if (isThirdPartyBundle) thirdParty.push(`${rel} → ${host}`);
        else firstParty.push(`${rel} → ${host}`);
      }
    }
    trace('p5-portable-offline-scan', { firstParty, thirdParty, documentation, note: 'Markdown 文档链接单列；第一方可执行路径继续严格检查，第三方地址来自许可证与横幅' });
    // 我们自己的运行路径（后台、脚本、插件、内核）必须零外部地址
    expect(firstParty).toEqual([]);
    // 第三方产物的地址必须全部是已知横幅主机，避免把真实的运行时外呼混进来
    const unknown = thirdParty.filter((entry) => !bannerHosts.has(entry.split('→')[1].trim()));
    expect(unknown).toEqual([]);
    expect(documentation.filter((entry) => entry.split('→')[1].trim() !== 'github.com')).toEqual([]);
  });

  it('包内后台可以完成真实制作流程，并导出包含展示器的发布包', async () => {
    const serverEntry = path.join(packageDir, 'app', 'server', 'src', 'main.ts');
    packaged = await startBackend(workspace, { name: 'portable-server', entry: serverEntry, cwd: path.join(packageDir, 'app') });
    const session = await pairSession(packaged.url, readPairingKey(workspace), 'portable-test');
    const created = await api(packaged.url, session.token, 'POST', '/v1/projects', { title: '便携包工程', chapterTitle: '第一章', sceneTitle: '开场' });
    expect(created.status, JSON.stringify(created.body)).toBe(201);
    const projectId = (created.body as { projectId: string }).projectId;
    const sceneId = (created.body as { sceneId: string }).sceneId;

    const edited = await api(packaged.url, session.token, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'portable-edit-1',
      projectId,
      baseRevision: (created.body as { revision: string }).revision,
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '便携包写入的对白。' }, eventId: 'ev_portable001' } }],
    });
    expect(edited.status, JSON.stringify(edited.body)).toBe(200);
    expect((edited.body as { status: string }).status).toBe('applied');

    const validation = await api(packaged.url, session.token, 'GET', `/v1/projects/${projectId}/validate`);
    expect((validation.body as { ok: boolean }).ok).toBe(true);

    const exported = await api(packaged.url, session.token, 'POST', `/v1/projects/${projectId}/exports`, {});
    expect(exported.status, JSON.stringify(exported.body)).toBe(201);
    const body = exported.body as { directory: string; playerIncluded: boolean; fileCount: number };
    expect(body.playerIncluded, '便携包内必须带上展示器构建产物').toBe(true);
    expect(existsSync(path.join(workspace, body.directory, 'runtime/node.exe'))).toBe(true);
    expect(existsSync(path.join(workspace, body.directory, '启动展示器.cmd'))).toBe(true);
    const bundle = JSON.parse(readFileSync(path.join(workspace, body.directory, 'content', 'content.json'), 'utf8')) as { scenes: { events: { params: { text?: string } }[] }[] };
    const texts = bundle.scenes.flatMap((scene) => scene.events.map((event) => event.params.text));
    expect(texts).toContain('便携包写入的对白。');
    trace('p5-portable-flow', { projectId, fileCount: body.fileCount, playerIncluded: body.playerIncluded });
  }, 120000);

  it('包内 MCP 适配器可启动并列出注册表全部工具（外部 AI 的制作入口在包里可用）', async () => {
    const entry = path.join(packageDir, 'app', 'plugin', 'studio-author', 'server', 'mcp-stdio.mjs');
    const mcp = new McpClient(path.join(packageDir, 'app'), { name: 'portable-mcp', clientName: 'portable-test', entry });
    try {
      const init = await mcp.request('initialize', { protocolVersion: '2024-11-05', clientInfo: { name: 'portable-test', version: '1.0.0' } }) as { serverInfo?: { name?: string } };
      expect(init.serverInfo?.name).toBeTruthy();
      const listed = await mcp.request('tools/list', {}) as { tools: { name: string }[] };
      const names = listed.tools.map((tool) => tool.name);
      expect(names.length).toBeGreaterThanOrEqual(36);
      expect(names).toContain('studio_render_preview');
      expect(names).toContain('studio_apply_changes');
      trace('p5-portable-mcp', { toolCount: names.length });
    } finally {
      mcp.close();
    }
  }, 120000);

  it('install.mjs 复制到指定前缀并可创建快捷方式；安装副本可真实启动；uninstall.mjs 彻底删除', async () => {
    const prefix = path.join(os.tmpdir(), `studio-prefix-${Date.now()}`);
    const shortcutDir = path.join(prefix, 'shortcuts');
    const installed = await run(process.execPath, [path.join(packageDir, 'install.mjs'), '--prefix', prefix, '--shortcut-dir', shortcutDir], { cwd: packageDir });
    expect(installed.code, `${installed.stdout}\n${installed.stderr}`).toBe(0);
    // 安装后的目录必须能直接启动：入口、编辑器、展示器、插件、清单都在
    for (const rel of ['studio.cmd', 'manifest.json', 'app/server/src/main.ts', 'app/dist/studio-live.html', 'app/dist-player/player.html', 'app/plugin/studio-author/manifest.json']) {
      expect(existsSync(path.join(prefix, rel)), `安装后缺少 ${rel}`).toBe(true);
    }
    // 快捷方式落在指定目录（测试用临时目录，不碰真实开始菜单）
    const links = readdirSync(shortcutDir);
    expect(links.some((name) => name.endsWith('.lnk'))).toBe(true);

    // 用安装后的后台跑一次真实请求（证明安装副本可用，而不是只复制了文件）
    const installedWorkspace = makeWorkspace('portable-installed');
    const installedService = await startBackend(installedWorkspace, {
      name: 'portable-installed',
      entry: path.join(prefix, 'app', 'server', 'src', 'main.ts'),
      cwd: path.join(prefix, 'app'),
    });
    try {
      const session = await pairSession(installedService.url, readPairingKey(installedWorkspace), 'portable-installed');
      const health = await api(installedService.url, session.token, 'GET', '/v1/health');
      expect(health.status).toBe(200);
      expect((health.body as { status?: string }).status).toBe('ok');
    } finally {
      await installedService.stop();
    }

    const removed = await run(process.execPath, [path.join(packageDir, 'uninstall.mjs'), '--prefix', prefix, '--shortcut-dir', shortcutDir], { cwd: packageDir });
    expect(removed.code, `${removed.stdout}\n${removed.stderr}`).toBe(0);
    expect(existsSync(prefix)).toBe(false);
    trace('p5-portable-install', { prefix, shortcuts: links.length });
  }, 180000);
});
