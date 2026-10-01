// P3 集成测试：插件包与制作 Skill。
// 覆盖：插件构建产物启动的真实 MCP 进程、Skill 引用的工具与 tools/list 一致、
//       按 Skill 流程经工具完成一章制作、素材导入与绑定、会话暂停后的拒绝、结构检查脚本。
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { McpClient, REPO_ROOT, api, makeWorkspace, pairSession, readPairingKey, startBackend, trace } from '../helpers/harness.ts';
import type { ServiceHandle } from '../helpers/harness.ts';
import { encodeSolidPng } from '../../../server/src/media/decode.ts';

const PLUGIN_DIR = path.join(REPO_ROOT, 'plugin', 'studio-author');
const PLUGIN_BUNDLE = path.join(PLUGIN_DIR, 'server', 'mcp-stdio.mjs');

let workspace = '';
let service: ServiceHandle;
let mcp: McpClient;
let token = '';
let sessionId = '';
let projectId = '';
let revision = '';
let chapterId = '';
let sceneId = '';
let backgroundEventId = '';

function run(command: string, args: string[]): Promise<{ code: number | null; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd: REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });
    child.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

beforeAll(async () => {
  // 插件构建产物必须先存在（npm run build:plugin）
  expect(existsSync(PLUGIN_BUNDLE), '插件构建产物缺失：请先运行 npm run build:plugin').toBe(true);
  workspace = makeWorkspace('p3');
  mkdirSync(path.join(workspace, 'imports'), { recursive: true });
  service = await startBackend(workspace, { name: 'p3' });
  const session = await pairSession(service.url, readPairingKey(workspace), 'p3-author-cli', ['read', 'edit', 'generate', 'preview', 'export', 'admin']);
  token = session.token;
  sessionId = session.sessionId;
  // 工程本身由人工/CLI 创建（编辑器“新建工程”路径），内容全部经 MCP 工具制作
  const created = await api(service.url, token, 'POST', '/v1/projects', { title: '雨夜旧塔（P3 插件验收）', chapterTitle: '第一章 塔前', sceneTitle: '雨夜' });
  const createdBody = created.body as { projectId: string; sceneId: string; revision: string };
  projectId = createdBody.projectId;
  sceneId = createdBody.sceneId;
  revision = createdBody.revision;
  mcp = new McpClient(workspace, { clientName: 'p3-adapter', env: { STUDIO_AUTOSTART: '0' }, name: 'p3', entry: PLUGIN_BUNDLE });
});

afterAll(async () => {
  await mcp?.close();
  await service?.stop();
});

describe('P3 插件包与 Skill 结构', () => {
  it('插件结构检查脚本通过（静态可验证部分）', async () => {
    const outFile = path.join(workspace, 'plugin-check.json');
    const result = await run(process.execPath, [path.join('scripts', 'studio-plugin-check.mjs'), '--json', outFile]);
    expect(result.code, result.stdout + result.stderr).toBe(0);
    const report = JSON.parse(readFileSync(outFile, 'utf8')) as { summary: { passed: number; failed: number; warnings: number }; checks: { id: string; status: string }[]; notVerifiedHere: string[] };
    expect(report.summary.failed).toBe(0);
    expect(report.summary.passed).toBeGreaterThanOrEqual(8);
    expect(report.checks.some((check) => check.id === 'PLUGIN-NO-FILE-WRITES' && check.status === 'passed')).toBe(true);
    expect(report.notVerifiedHere.join(' ')).toContain('宿主');
    trace('p3-plugin-check', { summary: report.summary, notVerifiedHere: report.notVerifiedHere });
  });

  it('插件构建产物可以启动并完成 MCP 握手', async () => {
    const result = await mcp.request('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'vitest', version: '1' } }) as Record<string, unknown>;
    expect(result.protocolVersion).toBe('2025-06-18');
    mcp.notify('notifications/initialized');
  });

  it('Skill 引用的工具都能在真实 tools/list 中找到，且参数规范可用', async () => {
    const skill = readFileSync(path.join(PLUGIN_DIR, 'skills', 'studio-author', 'SKILL.md'), 'utf8');
    const referenced = [...new Set([...skill.matchAll(/`(studio_[a-z_]+)`/g)].map((match) => match[1]))].sort();
    const listed = await mcp.request('tools/list', {}) as { tools: { name: string; inputSchema: { type?: string; properties?: Record<string, unknown> }; description?: string }[] };
    const names = listed.tools.map((tool) => tool.name);
    const missing = referenced.filter((name) => !names.includes(name));
    expect(missing, `Skill 引用了不存在的工具：${missing.join(', ')}`).toEqual([]);
    for (const tool of listed.tools) {
      expect(tool.inputSchema?.type, `${tool.name} 缺少对象型 inputSchema`).toBe('object');
      expect(tool.description && tool.description.length > 8, `${tool.name} 缺少说明`).toBe(true);
    }
    expect(names).toContain('studio_apply_changes');
    expect(names).toContain('studio_start_preview');
    expect(names).toContain('studio_export_project');
    trace('p3-tools', { referenced: referenced.length, listed: names.length, names });
  });
});

describe('P3 按 Skill 流程经工具制作一章', () => {
  it('查询能力与工程范围', async () => {
    const capabilities = await mcp.callTool('studio_get_capabilities', {});
    expect(capabilities.ok).toBe(true);
    const features = (capabilities.data as { features: Record<string, unknown> }).features;
    expect(features.preview).toBe(true);
    expect(features.mediaJobs).toBe(true);
    // 生成能力必须如实声明“只有测试适配器”，不能让人误以为已接入真实服务
    expect(JSON.stringify(features.mediaProviders)).toContain('测试适配器');
    const unsupported = JSON.stringify((capabilities.data as { unsupported: string[] }).unsupported);
    expect(unsupported).toContain('真实图片/视频服务接入');

    const projects = await mcp.callTool('studio_list_projects', {});
    expect(projects.ok).toBe(true);
    expect((projects.data as { projects: { projectId: string }[] }).projects.map((item) => item.projectId)).toContain(projectId);

    const opened = await mcp.callTool('studio_open_project', { projectId });
    expect((opened.data as { revision: string }).revision).toBe(revision);
  });

  it('创建章节与场景', async () => {
    const chapter = await mcp.callTool('studio_create_chapter', { projectId, title: '第二章 塔内', baseRevision: revision, requestId: 'p3-chapter' });
    expect(chapter.ok, JSON.stringify(chapter.error)).toBe(true);
    chapterId = (chapter.data as { changedIds: string[] }).changedIds.find((id) => id.startsWith('ch_')) as string;
    revision = (chapter.data as { revision: string }).revision;

    const scene = await mcp.callTool('studio_create_scene', { projectId, chapterId, title: '石阶', baseRevision: revision, requestId: 'p3-scene' });
    expect(scene.ok, JSON.stringify(scene.error)).toBe(true);
    revision = (scene.data as { revision: string }).revision;
    trace('p3-structure', { chapterId, revision });
  });

  it('导入背景素材并绑定到背景事件', async () => {
    const pngPath = path.join(workspace, 'imports', 'tower-night.png');
    writeFileSync(pngPath, encodeSolidPng(16, 10, [18, 24, 38]));
    const imported = await mcp.callTool('studio_import_asset', { projectId, sourcePath: pngPath, name: '雨夜塔内', kind: 'background', baseRevision: revision, requestId: 'p3-asset' });
    expect(imported.ok, JSON.stringify(imported.error)).toBe(true);
    const assetId = (imported.data as { changedIds: string[] }).changedIds.find((id) => id.startsWith('as_')) as string;
    revision = (imported.data as { revision: string }).revision;

    const inserted = await mcp.callTool('studio_insert_event', {
      projectId, sceneId, type: 'background', params: { assetId }, eventId: 'ev_p3bg000001', baseRevision: revision, requestId: 'p3-bg',
    });
    expect(inserted.ok, JSON.stringify(inserted.error)).toBe(true);
    backgroundEventId = 'ev_p3bg000001';
    revision = (inserted.data as { revision: string }).revision;

    const invalidBind = await mcp.callTool('studio_bind_asset', { projectId, sceneId, eventId: backgroundEventId, field: 'assetId', assetId: 'as_missing00001', baseRevision: revision });
    expect(invalidBind.ok).toBe(false);
    expect(invalidBind.error?.code).toBe('VALIDATION_FAILED');

    const bound = await mcp.callTool('studio_bind_asset', { projectId, sceneId, eventId: backgroundEventId, field: 'assetId', assetId, baseRevision: revision, requestId: 'p3-bind' });
    expect(bound.ok, JSON.stringify(bound.error)).toBe(true);
    revision = (bound.data as { revision: string }).revision;
  });

  it('用一次事务写入对白与选择（关联修改整体提交）', async () => {
    const scene2 = 'sc_p3inside001';
    const ensureScene = await mcp.callTool('studio_create_scene', { projectId, chapterId, title: '塔顶', sceneId: scene2, baseRevision: revision, requestId: 'p3-scene2' });
    expect(ensureScene.ok, JSON.stringify(ensureScene.error)).toBe(true);
    revision = (ensureScene.data as { revision: string }).revision;

    const batched = await mcp.callTool('studio_apply_changes', {
      projectId,
      baseRevision: revision,
      requestId: 'p3-batch',
      summary: '第二章开场：对白与选择',
      operations: [
        { action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '雨点敲在旧塔的铁门上。' }, eventId: 'ev_p3line00001' } },
        { action: 'event.insert', payload: { sceneId, type: 'choice', params: { prompt: '要进塔吗？', options: [{ label: '进塔', targetSceneId: scene2 }] }, eventId: 'ev_p3choice001' } },
        { action: 'event.insert', payload: { sceneId: scene2, type: 'ending', params: { endingId: 'end_tower', title: '塔顶' }, eventId: 'ev_p3ending001' } },
      ],
    });
    expect(batched.ok, JSON.stringify(batched.error)).toBe(true);
    revision = (batched.data as { revision: string }).revision;

    const outline = await mcp.callTool('studio_get_project', { projectId });
    const outlineBody = outline.data as { outline: { chapterId: string; scenes: { sceneId: string; eventCount: number }[] }[] };
    expect(outlineBody.outline).toHaveLength(2);
    const newChapter = outlineBody.outline.find((chapter) => chapter.chapterId === chapterId);
    expect(newChapter?.scenes.map((scene) => scene.sceneId)).toContain(scene2);
  });

  it('校验、预览、渲染与保存检查点', async () => {
    const validation = await mcp.callTool('studio_validate_project', { projectId });
    expect(validation.ok).toBe(true);
    expect((validation.data as { ok: boolean }).ok, JSON.stringify((validation.data as { issues: unknown[] }).issues)).toBe(true);

    const preview = await mcp.callTool('studio_start_preview', { projectId, sceneId });
    expect(preview.ok).toBe(true);
    const previewId = (preview.data as { previewId: string }).previewId;

    const advanced = await mcp.callTool('studio_preview_action', { projectId, previewId, type: 'advance' });
    expect((advanced.data as { status: string }).status).toBe('awaitingChoice');
    const chosen = await mcp.callTool('studio_preview_action', { projectId, previewId, type: 'choose', option: 0 });
    expect((chosen.data as { sceneId: string }).sceneId).toBe('sc_p3inside001');
    const ended = await mcp.callTool('studio_preview_action', { projectId, previewId, type: 'advance' });
    expect((ended.data as { status: string }).status).toBe('ended');

    const render = await mcp.callTool('studio_render_preview', { projectId, previewId });
    expect(render.ok).toBe(true);
    expect((render.data as { note: string }).note).toContain('不是像素级渲染');

    const checkpoint = await mcp.callTool('studio_save_project', { projectId });
    expect(checkpoint.ok).toBe(true);
    expect((checkpoint.data as { verifiedFromDisk: boolean }).verifiedFromDisk).toBe(true);
    expect((checkpoint.data as { revision: string }).revision).toBe(revision);
    trace('p3-flow', { previewId, revision, checkpoint: checkpoint.data });
  });

  it('工具写入的内容对人工界面接口与磁盘一致可见', async () => {
    const detail = await api(service.url, token, 'GET', `/v1/projects/${projectId}/scenes/${sceneId}`);
    const events = (detail.body as { events?: { eventId: string; summary: string }[] }).events ?? [];
    expect(detail.status, JSON.stringify(detail.body).slice(0, 300)).toBe(200);
    expect(events.map((event) => event.summary).join(' | '), '场景事件摘要应包含对白文本').toContain('雨点敲在旧塔的铁门上。');

    const sceneFile = path.join(workspace, projectId, 'story', 'scenes', `${sceneId}.json`);
    const onDisk = JSON.parse(readFileSync(sceneFile, 'utf8')) as { events: { eventId: string; params: { text?: string } }[] };
    expect(onDisk.events.some((event) => event.params.text === '雨点敲在旧塔的铁门上。')).toBe(true);

    const history = await mcp.callTool('studio_get_history', { projectId });
    const transactions = (history.data as { transactions: { actor: string; requestId: string }[] }).transactions;
    // 操作者身份由后台会话得出：这里必须是插件适配器自己的会话，而不是调用者自报的名字
    expect(transactions.some((item) => item.actor === 'ai:p3-adapter')).toBe(true);
    expect(transactions.find((item) => item.requestId === 'p3-batch')).toBeTruthy();
  });
});

describe('P3 会话授权与写入暂停', () => {
  it('人工暂停该 AI 会话后，工具写入被拒绝；恢复后可以继续', async () => {
    // 人工界面会话（用于暂停入口），管理员会话（用于定位插件适配器真正使用的会话）
    const ui = await fetch(`${service.url}/v1/ui-session`, { method: 'POST', headers: { origin: 'http://127.0.0.1:5173' } }).then((response) => response.json()) as { token: string };
    const listed = await api(service.url, token, 'GET', '/v1/sessions');
    const adapterSession = (listed.body as { sessions: { sessionId: string; actorId: string; channel: string }[] }).sessions.find((item) => item.actorId === 'ai:p3-adapter');
    expect(adapterSession, '应能找到插件适配器建立的会话').toBeTruthy();

    const paused = await api(service.url, ui.token, 'POST', `/v1/ai-sessions/${adapterSession!.sessionId}/pause`, {});
    expect(paused.status).toBe(200);

    const blocked = await mcp.callTool('studio_insert_event', { projectId, sceneId, type: 'wait', params: { ms: 50 }, baseRevision: revision });
    expect(blocked.ok, JSON.stringify(blocked.data)).toBe(false);
    expect(blocked.error?.code).toBe('SESSION_PAUSED');

    const resumed = await api(service.url, ui.token, 'POST', `/v1/ai-sessions/${adapterSession!.sessionId}/resume`, {});
    expect(resumed.status).toBe(200);
    const allowed = await mcp.callTool('studio_insert_event', { projectId, sceneId, type: 'wait', params: { ms: 50 }, baseRevision: revision, requestId: 'p3-after-resume' });
    expect(allowed.ok, JSON.stringify(allowed.error)).toBe(true);
    revision = (allowed.data as { revision: string }).revision;
    trace('p3-pause', { adapterSessionId: adapterSession!.sessionId, blockedCode: blocked.error?.code, revisionAfterResume: revision });
  });

  it('后台不可用时插件返回 BACKEND_UNAVAILABLE 且不产生任何工程文件', async () => {
    const freshWorkspace = makeWorkspace('p3-down');
    const client = new McpClient(freshWorkspace, { clientName: 'p3-down', env: { STUDIO_AUTOSTART: '0' }, name: 'p3-down', entry: PLUGIN_BUNDLE });
    try {
      const result = await client.callTool('studio_list_projects', {});
      expect(result.ok).toBe(false);
      expect(result.error?.code).toBe('BACKEND_UNAVAILABLE');
      expect(existsSync(path.join(freshWorkspace, 'projects.json'))).toBe(false);
    } finally {
      await client.close();
    }
  });
});
