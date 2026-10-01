// P0 退出标准集成测试：编辑器窗口关闭时，通过真实 MCP 工具/HTTP API 制作并保存；
// 终止并重启后台后读到相同数据；全过程不使用桌面自动化，也不直接写工程文件。
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import {
  McpClient, api, delay, discoveryHasSecrets, makeWorkspace, pairSession, readDiscoveryRaw,
  readPairingKey, startBackend, trace,
} from '../helpers/harness.ts';
import type { ServiceHandle } from '../helpers/harness.ts';

let workspace = '';
let service: ServiceHandle;
let mcp: McpClient;
let token = '';
let projectId = '';
let chapterId = '';
let sceneId = '';
let revision = '';
let dialogueEventId = '';
let dialogueRequestArgs: Record<string, unknown> = {};
const revisionTrail: { step: string; revision: string }[] = [];

beforeAll(async () => {
  workspace = makeWorkspace('p0');
  service = await startBackend(workspace, { name: 'p0' });
  const session = await pairSession(service.url, readPairingKey(workspace), 'p0-ai-author');
  token = session.token;
  mcp = new McpClient(workspace, { clientName: 'p0-ai-author', env: { STUDIO_AUTOSTART: '0' }, name: 'p0' });
});

afterAll(async () => {
  await mcp?.close();
  await service?.stop();
});

describe('P0-1 后台能力查询与工程创建（HTTP API，人工/CLI 路径）', () => {
  it('健康检查可用且发现记录不含凭据', async () => {
    const response = await fetch(`${service.url}/v1/health`);
    const body = await response.json() as Record<string, unknown>;
    expect(response.status).toBe(200);
    expect(body.instanceId).toBe(service.instanceId);
    expect(discoveryHasSecrets(workspace)).toEqual([]);
    const discovery = readDiscoveryRaw(workspace);
    expect(discovery?.url).toBe(service.url);
    trace('p0-health', { url: service.url, instanceId: service.instanceId, discoveryKeys: Object.keys(discovery ?? {}) });
  });

  it('capabilities 如实声明能力（预览/媒体尚未实现）', async () => {
    const response = await api(service.url, token, 'GET', '/v1/capabilities');
    expect(response.status).toBe(200);
    const body = response.body as { features: Record<string, unknown>; unsupported: string[]; events: { type: string }[]; tools: { name: string }[] };
    expect(body.features.transactions).toBe(true);
    // 媒体生成：只声明测试适配器可用，真实服务在 unsupported 中单独列出
    expect(body.features.mediaJobs).toBe(true);
    expect(JSON.stringify(body.features.mediaProviders)).toContain('测试适配器');
    expect(body.unsupported.join(' ')).toContain('真实图片/视频服务接入');
    expect(body.events.map((event) => event.type)).toContain('dialogue');
    expect(body.tools.map((tool) => tool.name)).toContain('studio_insert_event');
    expect(body.unsupported.length).toBeGreaterThan(0);
  });

  it('创建工程返回稳定 ID 与修订 0', async () => {
    const response = await api(service.url, token, 'POST', '/v1/projects', { title: '雨夜旧塔', chapterTitle: '第一章', sceneTitle: '塔前' });
    expect(response.status).toBe(201);
    const body = response.body as { projectId: string; revision: string; chapterId: string; sceneId: string };
    projectId = body.projectId;
    chapterId = body.chapterId;
    sceneId = body.sceneId;
    revision = body.revision;
    expect(projectId).toMatch(/^prj_/);
    expect(revision).toBe('0');
    revisionTrail.push({ step: '创建工程', revision });
    expect(existsSync(path.join(workspace, projectId, 'project.json'))).toBe(true);
  });
});

describe('P0-2 通过 MCP 工具制作对白（无编辑器窗口）', () => {
  it('MCP 握手返回协议版本与工具能力', async () => {
    const result = await mcp.request('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'vitest', version: '1' } }) as Record<string, unknown>;
    expect(result.protocolVersion).toBe('2025-06-18');
    expect((result.capabilities as { tools: unknown }).tools).toBeTruthy();
    mcp.notify('notifications/initialized');
  });

  it('tools/list 只声明已经实现的工具', async () => {
    const result = await mcp.request('tools/list', {}) as { tools: { name: string; inputSchema: Record<string, unknown> }[] };
    const names = result.tools.map((tool) => tool.name);
    for (const expected of ['studio_get_capabilities', 'studio_open_project', 'studio_read_scene', 'studio_insert_event', 'studio_apply_changes', 'studio_validate_project', 'studio_save_project', 'studio_undo']) {
      expect(names).toContain(expected);
    }
    const insert = result.tools.find((tool) => tool.name === 'studio_insert_event')!;
    expect(insert.inputSchema.type).toBe('object');
    expect((insert.inputSchema.required as string[])).toContain('params');
    trace('p0-tools', { count: names.length, names });
  });

  it('打开工程读到当前修订', async () => {
    const result = await mcp.callTool('studio_open_project', { projectId });
    expect(result.ok).toBe(true);
    expect((result.data as { revision: string }).revision).toBe('0');
  });

  it('插入对白后修订递增并落盘', async () => {
    dialogueRequestArgs = { projectId, sceneId, type: 'dialogue', params: { speaker: '璃', text: '雨点敲在旧塔的铁门上。' } };
    const result = await mcp.callTool('studio_insert_event', dialogueRequestArgs);
    expect(result.ok).toBe(true);
    expect(result.status).toBe('applied');
    const data = result.data as { revision: string; changedIds: string[]; persistedRevision: string; requestId: string };
    revision = data.revision;
    dialogueEventId = data.changedIds.find((id) => id.startsWith('ev_'))!;
    revisionTrail.push({ step: 'MCP 插入对白', revision });
    expect(data.persistedRevision).toBe(revision);
    // 直接读文件确认内容已经写入磁盘（不是内存）
    const sceneFile = path.join(workspace, projectId, 'story', 'scenes', `${sceneId}.json`);
    const scene = JSON.parse(readFileSync(sceneFile, 'utf8')) as { events: { eventId: string; params: { text: string } }[] };
    expect(scene.events).toHaveLength(1);
    expect(scene.events[0].params.text).toBe('雨点敲在旧塔的铁门上。');
  });

  it('同参数重试复用 requestId，不重复创建', async () => {
    const again = await mcp.callTool('studio_insert_event', dialogueRequestArgs);
    expect(again.ok).toBe(true);
    expect(again.data?.replayed).toBe(true);
    expect((again.data as { revision: string }).revision).toBe(revision);
    const scene = await mcp.callTool('studio_read_scene', { projectId, sceneId });
    expect((scene.data as { events: unknown[] }).events).toHaveLength(1);
  });

  it('MCP 可以继续创建第二章与场景，形成两章结构', async () => {
    const chapter = await mcp.callTool('studio_create_chapter', { projectId, title: '第二章 塔内', baseRevision: revision });
    expect(chapter.ok).toBe(true);
    const newChapterId = (chapter.data as { changedIds: string[] }).changedIds.find((id) => id.startsWith('ch_'))!;
    const scene = await mcp.callTool('studio_create_scene', { projectId, chapterId: newChapterId, title: '石阶', baseRevision: (chapter.data as { revision: string }).revision });
    expect(scene.ok).toBe(true);
    const newSceneId = (scene.data as { changedIds: string[] }).changedIds.find((id) => id.startsWith('sc_'))!;
    revision = (scene.data as { revision: string }).revision;
    revisionTrail.push({ step: 'MCP 创建第二章与场景', revision });
    const dialogue = await mcp.callTool('studio_insert_event', { projectId, sceneId: newSceneId, type: 'dialogue', params: { speaker: '璃', text: '石阶上有新的脚印。' }, baseRevision: revision });
    expect(dialogue.ok).toBe(true);
    revision = (dialogue.data as { revision: string }).revision;
    revisionTrail.push({ step: 'MCP 在第二章插入对白', revision });
    const outline = await mcp.callTool('studio_get_project', { projectId });
    const data = outline.data as { outline: { scenes: unknown[] }[] };
    expect(data.outline).toHaveLength(2);
  });

  it('校验工程无结构问题并保存检查点', async () => {
    const validation = await mcp.callTool('studio_validate_project', { projectId });
    expect(validation.ok).toBe(true);
    expect((validation.data as { ok: boolean }).ok).toBe(true);
    const checkpoint = await mcp.callTool('studio_save_project', { projectId });
    expect(checkpoint.ok).toBe(true);
    const data = checkpoint.data as { revision: string; persistedRevision: string; verifiedFromDisk: boolean; contentDigest: string };
    expect(data.revision).toBe(revision);
    expect(data.persistedRevision).toBe(revision);
    expect(data.verifiedFromDisk).toBe(true);
    expect(data.contentDigest).toMatch(/^[0-9a-f]{64}$/);
  });

  it('未授权请求被拒绝（不依赖客户端自报身份）', async () => {
    const anonymous = await fetch(`${service.url}/v1/projects`, { headers: { authorization: 'Bearer not-a-real-token' } });
    expect(anonymous.status).toBe(401);
    const forged = await fetch(`${service.url}/v1/projects/${projectId}/transactions`, {
      method: 'POST',
      headers: { authorization: 'Bearer not-a-real-token', 'content-type': 'application/json' },
      body: JSON.stringify({ requestId: 'forged', baseRevision: revision, operations: [], actor: 'human:admin' }),
    });
    expect(forged.status).toBe(401);
  });
});

describe('P0-3 终止并重启后台后读到相同数据', () => {
  it('强杀后台进程后重启，修订与内容一致', async () => {
    const before = revision;
    await service.stop('SIGKILL');
    await delay(300);
    service = await startBackend(workspace, { name: 'p0-restart' });
    const session = await pairSession(service.url, readPairingKey(workspace), 'p0-ai-author-after-restart');
    token = session.token;
    const project = await api(service.url, token, 'GET', `/v1/projects/${projectId}`);
    expect(project.status).toBe(200);
    const body = project.body as { revision: string; summary: { eventCount: number; chapterCount: number; sceneCount: number } };
    expect(body.revision).toBe(before);
    expect(body.summary.eventCount).toBe(2);
    expect(body.summary.chapterCount).toBe(2);
    trace('p0-restart', { revisionBefore: before, revisionAfterRestart: body.revision, summary: body.summary });
  });

  it('重启后 MCP 新进程读到同一工程与同一条对白', async () => {
    await mcp.close();
    mcp = new McpClient(workspace, { clientName: 'p0-ai-author-2', env: { STUDIO_AUTOSTART: '0' }, name: 'p0-restart' });
    const result = await mcp.callTool('studio_read_scene', { projectId, sceneId });
    expect(result.ok).toBe(true);
    const events = (result.data as { events: { eventId: string; type: string; params: { text?: string } }[] }).events;
    expect(events).toHaveLength(1);
    expect(events[0].eventId).toBe(dialogueEventId);
    expect(events[0].params.text).toBe('雨点敲在旧塔的铁门上。');
  });

  it('界面读取接口返回同一修订（供人工视图显示）', async () => {
    const response = await api(service.url, token, 'GET', `/v1/projects/${projectId}/scenes/${sceneId}`);
    const body = response.body as { revision: string; events: { summary: string }[] };
    expect(body.revision).toBe(revision);
    expect(body.events[0].summary).toContain('雨点敲在旧塔的铁门上。');
  });

  it('记录修订轨迹用于审查复现', () => {
    trace('p0-revision-trail', { workspace, projectId, trail: revisionTrail });
    expect(revisionTrail.map((entry) => entry.revision)).toEqual(['0', '1', '3', '4']);
  });
});

describe('P0-4 后台不可用时的行为', () => {
  it('无后台且未开启自动启动时返回 BACKEND_UNAVAILABLE，不回退到界面自动化', async () => {
    const emptyWorkspace = makeWorkspace('p0-down');
    const client = new McpClient(emptyWorkspace, { clientName: 'p0-no-backend', env: { STUDIO_AUTOSTART: '0' }, name: 'p0-down' });
    try {
      await client.request('initialize', { protocolVersion: '2025-06-18', capabilities: {} });
      const result = await client.callTool('studio_list_projects', {});
      expect(result.ok).toBe(false);
      expect(result.error?.code).toBe('BACKEND_UNAVAILABLE');
    } finally {
      await client.close();
    }
  });

  it('开启自动启动时 MCP 可以拉起后台并完成查询', async () => {
    const freshWorkspace = makeWorkspace('p0-autostart');
    const client = new McpClient(freshWorkspace, { clientName: 'p0-autostart', env: { STUDIO_AUTOSTART: '1' }, name: 'p0-autostart' });
    try {
      await client.request('initialize', { protocolVersion: '2025-06-18', capabilities: {} });
      const result = await client.callTool('studio_list_projects', {});
      expect(result.ok).toBe(true);
      const discovery = readDiscoveryRaw(freshWorkspace);
      expect(discovery).toBeTruthy();
      trace('p0-autostart', { workspace: freshWorkspace, discovery: { url: discovery?.url, pid: discovery?.pid } });
      // 清理自动拉起的后台进程
      const pid = Number(discovery?.pid ?? 0);
      if (pid) {
        try { process.kill(pid, 'SIGKILL'); } catch { /* 已退出 */ }
      }
    } finally {
      await client.close();
    }
  }, 40000);
});
