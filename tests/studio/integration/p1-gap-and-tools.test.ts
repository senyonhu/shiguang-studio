// 补齐两处长期缺口：
//   KI-004：变更流被截断时（after 早于保留窗口）必须返回 gap=true，客户端据此重新取快照。
//   KI-013：此前只有“同链路其它用例间接覆盖”的工具（搜索、素材列表、事件目录、删除事件）在这里有专门的用例。
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { api, makeWorkspace, McpClient, pairSession, readPairingKey, startBackend, trace } from '../helpers/harness.ts';
import type { ServiceHandle } from '../helpers/harness.ts';
import { buildStory } from '../helpers/story.ts';


/** 工具返回形如 { ok, tool, data }，这里统一取出 data（没有则用整个对象）。 */
function unwrap(text: string): Record<string, unknown> {
  const parsed = JSON.parse(text) as Record<string, unknown>;
  const data = parsed.data;
  return data && typeof data === 'object' ? data as Record<string, unknown> : parsed;
}

let workspace = '';
let service: ServiceHandle;
let token = '';
let projectId = '';
let revision = '';

beforeAll(async () => {
  workspace = makeWorkspace('p18-gaps');
  // 变更流只保留 3 条：便于验证截断（gap）路径
  service = await startBackend(workspace, { name: 'p18-gaps', env: { STUDIO_CHANGE_LIMIT: '3' } });
  const session = await pairSession(service.url, readPairingKey(workspace), 'p18-gaps');
  token = session.token;
  const story = await buildStory(service.url, token, workspace);
  projectId = story.projectId;
  revision = story.revision;
});

afterAll(async () => {
  await service?.stop();
});

async function insert(text: string, requestId: string, eventId: string): Promise<string> {
  const outline = await api(service.url, token, 'GET', `/v1/projects/${projectId}/outline`);
  const sceneId = (outline.body as { outline: { scenes: { sceneId: string }[] }[] }).outline[0].scenes[0].sceneId;
  const result = await api(service.url, token, 'POST', `/v1/projects/${projectId}/transactions`, {
    requestId,
    projectId,
    baseRevision: revision,
    operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text }, eventId } }],
  });
  // 注意：被拒绝的事务同样返回 HTTP 200，必须检查响应体里的 status
  expect(result.status, JSON.stringify(result.body)).toBe(200);
  expect((result.body as { status: string }).status, JSON.stringify(result.body)).toBe('applied');
  revision = (result.body as { revision: string }).revision;
  return sceneId;
}

describe('KI-004 变更流截断与 gap', () => {
  it('after 落在保留窗口之前报 gap=true；恰好在窗口起点则不报 gap 且不漏事件', async () => {
    // 保留窗口只有 3 条，而样板工程本身已提交多次：窗口已经滑过 0
    const atZero = await api(service.url, token, 'GET', `/v1/projects/${projectId}/changes?after=0`);
    expect(atZero.status).toBe(200);
    const firstFeed = atZero.body as { gap: boolean; oldestSequence: number; sequence: number; changes: { sequence: number }[] };
    expect(firstFeed.oldestSequence).toBeGreaterThan(1);
    expect(firstFeed.gap, 'after=0 已早于保留窗口，必须报 gap 让客户端重取快照').toBe(true);
    expect(firstFeed.changes.length).toBe(3);

    // 边界：after = oldest - 1 表示“已看到被丢弃的最后一条”，仍然能连续增量，不报 gap
    const boundary = await api(service.url, token, 'GET', `/v1/projects/${projectId}/changes?after=${firstFeed.oldestSequence - 1}`);
    expect((boundary.body as { gap: boolean }).gap, 'after = oldest-1 落在窗口边界内，不应报 gap').toBe(false);

    // 窗口起点：不报 gap，且返回的变更序号都大于 after
    const atOldest = await api(service.url, token, 'GET', `/v1/projects/${projectId}/changes?after=${firstFeed.oldestSequence}`);
    const inside = atOldest.body as { gap: boolean; changes: { sequence: number }[] };
    expect(inside.gap).toBe(false);
    expect(inside.changes.every((change) => change.sequence > firstFeed.oldestSequence)).toBe(true);

    // 追加两次提交后窗口滑动：最早序号变大，而 0 依旧报 gap
    await insert('窗口滑动用例第一句。', 'gap-slide-1', 'ev_gapslide001');
    await insert('窗口滑动用例第二句。', 'gap-slide-2', 'ev_gapslide002');
    const slid = await api(service.url, token, 'GET', `/v1/projects/${projectId}/changes?after=${firstFeed.sequence}`);
    const slidFeed = slid.body as { gap: boolean; oldestSequence: number; sequence: number };
    expect(slidFeed.oldestSequence).toBeGreaterThan(firstFeed.oldestSequence);
    expect(slidFeed.gap).toBe(false);
    const zeroAgain = await api(service.url, token, 'GET', `/v1/projects/${projectId}/changes?after=0`);
    expect((zeroAgain.body as { gap: boolean }).gap).toBe(true);
    trace('ki-004-change-gap', {
      oldestAtStart: firstFeed.oldestSequence,
      oldestAfterSlide: slidFeed.oldestSequence,
      sequence: slidFeed.sequence,
      boundaryGap: (boundary.body as { gap: boolean }).gap,
    });
  }, 120000);
});

describe('KI-013 此前缺少专门用例的工具（经真实 MCP 进程）', () => {
  it('studio_search / studio_list_assets / studio_get_event_catalog / studio_delete_event 行为符合契约', async () => {
    const mcp = new McpClient(workspace, { name: 'p18-tools', clientName: 'p18-tools' });
    try {
      await mcp.request('initialize', { protocolVersion: '2024-11-05', clientInfo: { name: 'p18-tools', version: '1.0.0' } });

      // 事件目录：返回全部事件类型与参数规范（工具契约，供 AI 查参数）
      const catalog = await mcp.request('tools/call', { name: 'studio_get_event_catalog', arguments: {} }) as { content: { text: string }[] };
      const catalogBody = unwrap(catalog.content[0].text) as { events?: { type: string; params?: unknown[] }[] };
      const events = catalogBody.events ?? [];
      const types = events.map((event) => event.type);
      expect(types).toContain('dialogue');
      expect(types).toContain('level');
      expect(types.length).toBeGreaterThanOrEqual(10);

      // 搜索：命中刚写入的文本，并能按场景定位
      const sceneId = await insert('搜索用例：塔顶的风声很轻。', 'tool-search', 'ev_toolsearch01');
      const search = await mcp.request('tools/call', { name: 'studio_search', arguments: { projectId, query: '塔顶的风声' } }) as { content: { text: string }[] };
      const hits = unwrap(search.content[0].text) as { matches?: { sceneId?: string; eventId?: string }[]; results?: { sceneId?: string }[] };
      const matches = hits.matches ?? hits.results ?? [];
      expect(matches.length).toBeGreaterThan(0);
      expect(matches.some((match) => match.sceneId === sceneId)).toBe(true);

      // 素材列表：包含样板工程导入的背景素材
      const assets = await mcp.request('tools/call', { name: 'studio_list_assets', arguments: { projectId } }) as { content: { text: string }[] };
      const assetBody = unwrap(assets.content[0].text) as { assets?: { assetId: string; kind: string }[] };
      expect(assetBody.assets?.length).toBeGreaterThan(0);
      expect(assetBody.assets?.some((asset) => asset.kind === 'background')).toBe(true);

      // 删除事件：真的删掉，并且再查该场景时不再返回
      const inserted = await api(service.url, token, 'GET', `/v1/projects/${projectId}/scenes/${sceneId}`);
      // 删掉刚插入的那条搜索用例事件（它在场景末尾）
      const targetEventId = (inserted.body as { events: { eventId: string }[] }).events.slice(-1)[0].eventId;
      const deleted = await mcp.request('tools/call', { name: 'studio_delete_event', arguments: { projectId, sceneId, eventId: targetEventId } }) as { content: { text: string }[] };
      const deleteBody = unwrap(deleted.content[0].text) as { status?: string; changedIds?: string[] };
      expect(deleteBody.status ?? 'applied').toBe('applied');
      const after = await api(service.url, token, 'GET', `/v1/projects/${projectId}/scenes/${sceneId}`);
      expect((after.body as { events: { eventId: string }[] }).events.some((event) => event.eventId === targetEventId)).toBe(false);
      const snapshot = await api(service.url, token, 'GET', `/v1/projects/${projectId}`);
      revision = (snapshot.body as { revision: string }).revision;
      trace('ki-013-tools', { catalogTypes: types.length, searchMatches: matches.length, assets: assetBody.assets?.length ?? 0, deletedEvent: targetEventId });
    } finally {
      await mcp.close();
    }
  }, 180000);
});
