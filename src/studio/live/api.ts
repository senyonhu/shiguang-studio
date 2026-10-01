// 编辑器（人工视图）使用的后台 API 客户端：所有读写都走同一个后台服务。
// 会话令牌只保存在内存中，不写入网址、工程或本地存储。
import type { AssetDoc, ChapterDoc, LevelDoc, StoryContent, VariableDoc } from '../../runtime/content.ts';
import type { EventType } from '../../runtime/events.ts';

export interface ProjectSummary {
  projectId: string;
  title: string;
  revision: string;
  chapterCount: number;
  sceneCount: number;
  eventCount: number;
  assetCount: number;
  updatedAt: string;
}

export interface Outline {
  revision: string;
  outline: { chapterId: string; title: string; scenes: { sceneId: string; title: string; eventCount: number }[] }[];
  characters: StoryContent['characters'];
  levels: LevelDoc[];
  variables: VariableDoc[];
  theme?: StoryContent['theme'];
  recipes?: StoryContent['recipes'];
}

export interface SceneEvent { index: number; eventId: string; type: EventType; summary: string; params: Record<string, unknown> }
export interface SceneDetail { revision: string; scene: { sceneId: string; chapterId: string; title: string }; events: SceneEvent[] }

export interface EditOutcome {
  status: 'applied' | 'conflict' | 'rejected';
  revision: string;
  persistedRevision: string;
  changedIds: string[];
  issues: { code: string; message: string; objectId?: string; field?: string }[];
  errorCode?: string;
  transactionId?: string;
  undoId?: string;
  replayed?: boolean;
  summary?: string;
}

export interface ChangeRecord {
  sequence: number;
  revision: string;
  transactionId: string;
  actor: string;
  changedIds: string[];
  summary: string;
  at: string;
}

export interface ProjectExport {
  exportId: string;
  directory: string;
  revision: string;
  fileCount: number;
  playerIncluded: boolean;
  portableIncluded: boolean;
  sensitiveHits: string[];
  missingAssets: string[];
  missingLevels: string[];
}

export class LiveApiError extends Error {
  code: string;
  issues: { code: string; message: string; objectId?: string; field?: string }[];
  constructor(code: string, message: string, issues: { code: string; message: string; objectId?: string; field?: string }[] = []) {
    super(message);
    this.code = code;
    this.issues = issues;
  }
}

export class LiveApi {
  baseUrl: string;
  private token = '';
  private sessionId = '';
  private actorId = '';

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
  }

  setBaseUrl(baseUrl: string): void {
    const url = new URL(baseUrl);
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.pathname !== '/' || url.search || url.hash || url.username || url.password) throw new LiveApiError('INVALID_BACKEND', '发现的制作服务地址无效');
    this.baseUrl = url.origin;
    this.token = '';
  }

  get session(): { sessionId: string; actorId: string } {
    return { sessionId: this.sessionId, actorId: this.actorId };
  }

  /** 本地人工会话：只对已授权来源发放，作用域不含 admin。 */
  async bootstrap(): Promise<void> {
    const response = await fetch(`${this.baseUrl}/v1/ui-session`, { method: 'POST', headers: { 'content-type': 'application/json' } });
    const body = await response.json().catch(() => ({})) as { token?: string; session?: { sessionId: string; actorId: string }; error?: { message?: string } };
    if (!response.ok || !body.token || !body.session) {
      throw new LiveApiError('UI_SESSION_FAILED', body.error?.message ?? `无法建立人工会话（HTTP ${response.status}）`);
    }
    this.token = body.token;
    this.sessionId = body.session.sessionId;
    this.actorId = body.session.actorId;
  }

  private async request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: { authorization: `Bearer ${this.token}`, 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    const parsed = text ? JSON.parse(text) as Record<string, unknown> : {};
    if (!response.ok) {
      const error = (parsed.error ?? {}) as { code?: string; message?: string; issues?: { code: string; message: string }[] };
      throw new LiveApiError(error.code ?? `HTTP_${response.status}`, error.message ?? '请求失败', error.issues ?? []);
    }
    return parsed as T;
  }

  health(): Promise<{ status: string; serviceVersion: string; apiVersion: string; pid: number }> {
    return this.request('GET', '/v1/health');
  }

  listProjects(): Promise<{ projects: ProjectSummary[] }> {
    return this.request('GET', '/v1/projects');
  }

  createProject(input: { title: string; projectId: string; chapterTitle?: string; sceneTitle?: string }): Promise<{ projectId: string; revision: string; chapterId: string; sceneId: string }> {
    return this.request('POST', '/v1/projects', input);
  }

  upload(projectId: string, input: { baseRevision: string; requestId: string; kind: string; name: string; fileName: string; dataBase64: string; levelId?: string; supportsSnapshot?: boolean }): Promise<EditOutcome> {
    return this.request('POST', `/v1/projects/${projectId}/uploads`, input);
  }

  playback(projectId: string, sceneId?: string): Promise<{ url: string; revision: string; expiresAt: string }> {
    return this.request('POST', `/v1/projects/${projectId}/playback`, { sceneId });
  }

  exportPlayback(projectId: string, exportId: string): Promise<{ url: string; revision: string; expiresAt: string }> {
    return this.request('POST', `/v1/projects/${projectId}/exports/${exportId}/playback`, {});
  }

  openExportDirectory(projectId: string, exportId: string): Promise<{ opened: boolean }> {
    return this.request('POST', `/v1/projects/${projectId}/exports/${exportId}/open-directory`, {});
  }

  async assetBlob(projectId: string, assetId: string): Promise<Blob> {
    const response = await fetch(this.assetUrl(projectId, assetId), { headers: { authorization: `Bearer ${this.token}` } });
    if (!response.ok) throw new LiveApiError('ASSET_UNAVAILABLE', '无法读取素材');
    return response.blob();
  }

  outline(projectId: string): Promise<Outline> {
    return this.request('GET', `/v1/projects/${projectId}/outline`);
  }

  scene(projectId: string, sceneId: string): Promise<SceneDetail> {
    return this.request('GET', `/v1/projects/${projectId}/scenes/${sceneId}`);
  }

  validate(projectId: string): Promise<{ ok: boolean; revision: string; issues: { path: string; message: string }[] }> {
    return this.request('GET', `/v1/projects/${projectId}/validate`);
  }

  exportProject(projectId: string): Promise<ProjectExport> {
    return this.request('POST', `/v1/projects/${projectId}/exports`, {});
  }

  history(projectId: string): Promise<{ transactions: { transactionId: string; sequence: number; summary: string; actor: string; at: string; undoOf?: string }[] }> {
    return this.request('GET', `/v1/projects/${projectId}/history`);
  }

  changes(projectId: string, after: number): Promise<{ revision: string; sequence: number; gap: boolean; changes: ChangeRecord[] }> {
    return this.request('GET', `/v1/projects/${projectId}/changes?after=${after}`);
  }

  assets(projectId: string): Promise<{ assets: AssetDoc[] }> {
    return this.request('GET', `/v1/projects/${projectId}/assets`);
  }

  aiSessions(): Promise<{ sessions: { sessionId: string; actorId: string; label: string; paused: boolean; createdAt: string; channel: string }[] }> {
    return this.request('GET', '/v1/ai-sessions');
  }

  setAiSessionPaused(sessionId: string, paused: boolean): Promise<{ session: { sessionId: string; paused: boolean } }> {
    return this.request('POST', `/v1/ai-sessions/${sessionId}/${paused ? 'pause' : 'resume'}`, {});
  }

  transaction(projectId: string, input: { baseRevision: string; operations: { action: string; payload: Record<string, unknown>; targetIds?: string[] }[]; requestId: string; summary?: string }): Promise<EditOutcome> {
    return this.request('POST', `/v1/projects/${projectId}/transactions`, {
      apiVersion: '1',
      requestId: input.requestId,
      projectId,
      baseRevision: input.baseRevision,
      operations: input.operations,
      summary: input.summary,
    });
  }

  undo(projectId: string, undoId: string, baseRevision: string): Promise<EditOutcome> {
    return this.request('POST', `/v1/projects/${projectId}/undo`, {
      requestId: `ui-undo-${undoId}-${Date.now()}`,
      undoId,
      baseRevision,
    });
  }

  subscribe(projectId: string, onEvent: (event: { revision: string; sequence: number; actor: string; changedIds: string[]; summary: string }) => void, onDisconnect?: () => void, onReady?: () => void): () => void {
    const controller = new AbortController();
    void (async () => {
      try {
        const response = await fetch(`${this.baseUrl}/v1/projects/${projectId}/events`, {
          headers: { authorization: `Bearer ${this.token}` },
          signal: controller.signal,
        });
        if (!response.ok) throw new Error('订阅连接不可用');
        const reader = response.body?.getReader();
        if (!reader) throw new Error('订阅连接不可用');
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
          const { done, value } = await reader.read();
          if (done) { if (!controller.signal.aborted) onDisconnect?.(); break; }
          buffer += decoder.decode(value, { stream: true });
          let index = buffer.indexOf('\n\n');
          while (index >= 0) {
            const block = buffer.slice(0, index);
            buffer = buffer.slice(index + 2);
            if (block.split('\n').some((line) => line.trim() === 'event: ready')) onReady?.();
            for (const line of block.split('\n')) {
              if (!line.startsWith('data: ')) continue;
              try {
                const parsed = JSON.parse(line.slice(6)) as { actor?: string; revision?: string };
                if (parsed.actor) onEvent(parsed as never);
              } catch { /* 忽略心跳等非 JSON 内容 */ }
            }
            index = buffer.indexOf('\n\n');
          }
        }
      } catch { if (!controller.signal.aborted) onDisconnect?.(); }
    })();
    return () => controller.abort();
  }

  assetUrl(projectId: string, assetId: string): string {
    return `${this.baseUrl}/v1/projects/${projectId}/assets/${assetId}/raw`;
  }
}

export function defaultApiBase(): string {
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get('api');
  if (fromQuery) return fromQuery;
  return 'http://127.0.0.1:5191';
}

export type { ChapterDoc, StoryContent };
