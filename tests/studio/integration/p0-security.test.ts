// P0/P1 安全与并发集成测试：认证、作用域、会话暂停、幂等冲突、变更流、本机限制。
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import {
  adminSession, api, delay, makeWorkspace, pairSession, rawRequest, readPairingKey, startBackend, subscribeChanges, trace,
} from '../helpers/harness.ts';
import type { ServiceHandle } from '../helpers/harness.ts';

let workspace = '';
let service: ServiceHandle;
let aiToken = '';
let aiSessionId = '';
let adminToken = '';
let projectId = '';
let sceneId = '';
let readOnlyToken = '';

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

beforeAll(async () => {
  workspace = makeWorkspace('security');
  service = await startBackend(workspace, { name: 'security' });
  const key = readPairingKey(workspace);
  const ai = await pairSession(service.url, key, 'security-ai');
  aiToken = ai.token;
  aiSessionId = ai.sessionId;
  const readonly = await pairSession(service.url, key, 'security-readonly', ['read']);
  readOnlyToken = readonly.token;
  const admin = await adminSession(service.url, workspace);
  adminToken = admin.token;
  const created = await api(service.url, aiToken, 'POST', '/v1/projects', { title: '安全测试工程', chapterTitle: '第一章', sceneTitle: '开场' });
  const body = created.body as { projectId: string; sceneId: string };
  projectId = body.projectId;
  sceneId = body.sceneId;
});

afterAll(async () => {
  await service?.stop();
});

describe('认证与作用域', () => {
  it('无令牌与伪造令牌都返回 401', async () => {
    const none = await fetch(`${service.url}/v1/projects`);
    expect(none.status).toBe(401);
    const forged = await fetch(`${service.url}/v1/projects`, { headers: { authorization: 'Bearer abcdef' } });
    expect(forged.status).toBe(401);
  });

  it('只读会话不能写入', async () => {
    const response = await api(service.url, readOnlyToken, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'readonly-write',
      projectId,
      baseRevision: '0',
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '不该写入' } } }],
    });
    expect(response.status).toBe(403);
    expect((response.body as { error: { code: string } }).error.code).toBe('SCOPE_REQUIRED');
  });

  it('客户端不能直接提交服务内部补偿操作', async () => {
    const response = await api(service.url, aiToken, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'internal-action',
      projectId,
      baseRevision: '0',
      operations: [{ action: 'chapter.restore', payload: { snapshot: { chapterId: 'ch_fake', title: '伪造', order: 0, sceneIds: [] } } }],
    });
    expect(response.status).toBe(422);
    expect((response.body as { error: { code: string } }).error.code).toBe('VALIDATION_FAILED');
  });

  it('非本机 Host 被拒绝', async () => {
    const response = await rawRequest(service.url, { path: '/v1/health', headers: { host: 'evil.example.com' } });
    expect(response.status).toBe(403);
    const localhost = await rawRequest(service.url, { path: '/v1/health', headers: { host: `127.0.0.1:${service.port}` } });
    expect(localhost.status).toBe(200);
  });

  it('人工界面会话只对已授权来源发放，且不含 admin 作用域', async () => {
    const rejected = await fetch(`${service.url}/v1/ui-session`, { method: 'POST', headers: { origin: 'http://evil.example.com' } });
    expect(rejected.status).toBe(403);
    const accepted = await fetch(`${service.url}/v1/ui-session`, { method: 'POST', headers: { origin: 'http://127.0.0.1:5173' } });
    expect(accepted.status).toBe(201);
    const body = await accepted.json() as { token: string; session: { scopes: string[]; actorId: string } };
    expect(body.session.scopes).not.toContain('admin');
    expect(body.session.actorId).toBe('human:local-ui');
    const sessions = await api(service.url, body.token, 'GET', '/v1/sessions');
    expect(sessions.status).toBe(403);
  });
});

describe('幂等与版本', () => {
  it('同一 requestId 不同内容返回 409', async () => {
    const first = await api(service.url, aiToken, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'dup-id',
      projectId,
      baseRevision: '0',
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '第一次内容' } } }],
    });
    expect((first.body as { status: string }).status).toBe('applied');
    const second = await api(service.url, aiToken, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'dup-id',
      projectId,
      baseRevision: '1',
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '不同内容' } } }],
    });
    expect(second.status).toBe(409);
    expect((second.body as { error: { code: string } }).error.code).toBe('REQUEST_ID_CONFLICT');
  });

  it('同一 requestId 同内容返回既有结果', async () => {
    const again = await api(service.url, aiToken, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'dup-id',
      projectId,
      baseRevision: '0',
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '第一次内容' } } }],
    });
    expect(again.status).toBe(200);
    const body = again.body as { status: string; replayed: boolean; revision: string };
    expect(body.status).toBe('applied');
    expect(body.replayed).toBe(true);
    expect(body.revision).toBe('1');
  });

  it('超时后可按 requestId 查询既有结果', async () => {
    const response = await api(service.url, aiToken, 'GET', `/v1/projects/${projectId}/transactions/dup-id`);
    expect(response.status).toBe(200);
    expect((response.body as { status: string }).status).toBe('applied');
  });
});

describe('会话暂停', () => {
  it('暂停后 AI 写入被拒绝，恢复后可以继续', async () => {
    const paused = await api(service.url, adminToken, 'POST', `/v1/sessions/${aiSessionId}/pause`, {});
    expect(paused.status).toBe(200);
    const blocked = await api(service.url, aiToken, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'paused-write',
      projectId,
      baseRevision: '1',
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '暂停期间不应写入' } } }],
    });
    expect(blocked.status).toBe(423);
    expect((blocked.body as { error: { code: string } }).error.code).toBe('SESSION_PAUSED');
    const readWhilePaused = await api(service.url, aiToken, 'GET', `/v1/projects/${projectId}`);
    expect(readWhilePaused.status).toBe(200);
    const resumed = await api(service.url, adminToken, 'POST', `/v1/sessions/${aiSessionId}/resume`, {});
    expect(resumed.status).toBe(200);
    const allowed = await api(service.url, aiToken, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'after-resume',
      projectId,
      baseRevision: '1',
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '恢复之后写入' } } }],
    });
    expect((allowed.body as { status: string }).status).toBe('applied');
  });

  it('会话列表不返回令牌摘要', async () => {
    const response = await api(service.url, adminToken, 'GET', '/v1/sessions');
    const sessions = (response.body as { sessions: { tokenHash?: string; sessionId: string; paused: boolean }[] }).sessions;
    expect(sessions.length).toBeGreaterThan(0);
    expect(sessions.every((session) => session.tokenHash === 'sha256:redacted')).toBe(true);
    trace('session-list', { count: sessions.length, ids: sessions.map((session) => session.sessionId) });
  });
});

describe('变更流', () => {
  it('SSE 收到提交事件，包含修订、变更对象与操作者', async () => {
    const received: Record<string, unknown>[] = [];
    const close = await subscribeChanges(service.url, aiToken, projectId, (event) => received.push(event));
    await delay(200);
    const current = await api(service.url, aiToken, 'GET', `/v1/projects/${projectId}`);
    const revision = (current.body as { revision: string }).revision;
    const committed = await api(service.url, aiToken, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'sse-change',
      projectId,
      baseRevision: revision,
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'wait', params: { ms: 100 } } }],
    });
    expect((committed.body as { status: string }).status).toBe('applied');
    for (let attempt = 0; attempt < 60 && received.filter((item) => typeof item.actor === 'string').length === 0; attempt += 1) await wait(50);
    close();
    const changeEvents = received.filter((item) => typeof item.actor === 'string');
    expect(changeEvents.length).toBeGreaterThan(0);
    const event = changeEvents[0];
    expect(event.projectId).toBe(projectId);
    expect(event.actor).toBe('ai:security-ai');
    expect(Array.isArray(event.changedIds)).toBe(true);
    expect(event.sequence).toBeGreaterThan(0);
    trace('sse-change', { received: received.slice(0, 3) });
  }, 30000);

  it('增量查询返回序号大于 after 的变更', async () => {
    const feed = await api(service.url, aiToken, 'GET', `/v1/projects/${projectId}/changes?after=1`);
    const body = feed.body as { sequence: number; gap: boolean; changes: { sequence: number }[] };
    expect(body.gap).toBe(false);
    expect(body.changes.every((change) => change.sequence > 1)).toBe(true);
    expect(body.sequence).toBeGreaterThanOrEqual(body.changes.length);
  });
});
