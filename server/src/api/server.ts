// 后台 HTTP API：唯一写入入口。人工界面与 MCP 工具都通过这里访问编辑服务。
// 只监听 127.0.0.1；校验 Host/Origin；错误统一返回 { error: { code, message, issues } }。
import http from 'node:http';
import { appendFileSync } from 'node:fs';
import { renderHtmlToPng } from '../preview/render.ts';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { URL } from 'node:url';
import type { AddressInfo } from 'node:net';
import { existsSync } from 'node:fs';
import { StudioError, asStudioError, httpStatusFor } from '../core/errors.ts';
import type { ActorRef, EditService } from '../edit/service.ts';
import { AuthService } from '../auth/sessions.ts';
import { workspacePaths } from '../store/paths.ts';
import type { WorkspacePaths } from '../store/paths.ts';
import { listProjects, summarizeProject } from '../store/workspace.ts';
import { API_VERSION, EVENT_CATALOG_VERSION, LEVEL_PROTOCOL_VERSION, MCP_PROTOCOL_VERSIONS, RUNTIME_KERNEL_VERSION, SERVICE_NAME, SERVICE_VERSION } from '../version.ts';
import { ACTIONS } from '../edit/actions.ts';
import { DEFAULT_THEME } from '../../../src/runtime/content.ts';
import { EVENT_SPECS } from '../../../src/runtime/events.ts';
import { sceneOutline } from '../../../src/runtime/content.ts';
import { TOOL_DEFINITIONS } from '../tools/registry.ts';
import { readBinary } from '../store/fsx.ts';
import { serviceUrl } from './urls.ts';
import type { PreviewService } from '../preview/service.ts';
import { exportProject, listExports } from '../publisher/service.ts';
import type { RuntimeAction } from '../../../src/runtime/kernel.ts';
import { isLevelMessage } from '../preview/service.ts';
import type { MediaJobService } from '../media/jobs.ts';
import { acceptMediaOutput } from '../media/accept.ts';
import type { MediaRequest } from '../media/types.ts';
import { applyUiUpload, UPLOAD_BODY_LIMIT } from './uploads.ts';
import { PlaybackLinks } from '../publisher/playback.ts';
import { openExportDirectory } from '../publisher/open-directory.ts';

const BODY_LIMIT = 4 * 1024 * 1024;
const UI_ORIGINS = ['http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:4173', 'http://localhost:4173', 'http://127.0.0.1:5190', 'http://localhost:5190'];

export interface StudioServerOptions {
  workspaceRoot: string;
  repoRoot: string;
  host?: string;
  port?: number;
  editService: EditService;
  auth: AuthService;
  previews: PreviewService;
  mediaJobs: MediaJobService;
  uiOrigins?: string[];
  instanceId: string;
  startedAt: string;
}

export interface StudioServer {
  server: http.Server;
  port: number;
  url: string;
  close: () => Promise<void>;
  emitChange: (event: unknown) => void;
}

export function createStudioServer(options: StudioServerOptions): StudioServer {
  const paths: WorkspacePaths = workspacePaths(options.workspaceRoot);
  const streams = new Set<{ res: ServerResponse; projectId: string }>();
  const uiOrigins = new Set(options.uiOrigins ?? UI_ORIGINS);
  const playback = new PlaybackLinks({ workspaceRoot: options.workspaceRoot, repoRoot: options.repoRoot });
  const playbackOrigin = (): string => serviceUrl(options.host ?? '127.0.0.1', (server.address() as AddressInfo | null)?.port ?? 0);

  const server = http.createServer((req, res) => {
    void handle(req, res).catch((error: unknown) => {
      const studioError = asStudioError(error);
      sendJson(res, httpStatusFor(studioError.code), studioError.toBody());
    });
  });

  function emitChange(event: unknown): void {
    const projectId = (event as { projectId?: string }).projectId ?? '';
    const payload = `event: change\ndata: ${JSON.stringify(event)}\n\n`;
    for (const stream of streams) {
      if (projectId && stream.projectId !== projectId) continue; // 只推送已订阅工程的变更
      try { stream.res.write(payload); } catch { streams.delete(stream); }
    }
  }

  const heartbeat = setInterval(() => {
    for (const stream of streams) {
      try { stream.res.write(': ping\n\n'); } catch { streams.delete(stream); }
    }
  }, 15000);
  heartbeat.unref?.();

  function cors(res: ServerResponse, origin: string | undefined): void {
    if (origin && uiOrigins.has(origin)) {
      res.setHeader('Access-Control-Allow-Origin', origin);
      res.setHeader('Vary', 'Origin');
      res.setHeader('Access-Control-Allow-Headers', 'authorization, content-type, mcp-protocol-version');
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS');
      res.setHeader('Access-Control-Max-Age', '600');
    }
  }

  function checkHost(req: IncomingMessage): void {
    const host = req.headers.host ?? '';
    const hostname = host.split(':')[0];
    if (!['127.0.0.1', 'localhost', '[::1]', '::1'].includes(hostname)) {
      throw new StudioError('FORBIDDEN', `只接受本机请求，收到 Host=${host || '(空)'}`);
    }
  }

  function requireScope(actor: ActorRef, scope: 'read' | 'edit' | 'generate' | 'preview' | 'export' | 'admin'): void {
    if (!options.editService.hasScope(actor, scope)) throw new StudioError('SCOPE_REQUIRED', `当前会话缺少 ${scope} 作用域`, { details: { scopes: actor.scopes } });
  }

  function authenticate(req: IncomingMessage): ActorRef {
    const header = req.headers.authorization ?? '';
    const match = /^Bearer\s+(.+)$/i.exec(header);
    if (!match) throw new StudioError('UNAUTHENTICATED', '缺少 Bearer 会话令牌');
    return options.auth.authenticate(match[1].trim());
  }

  async function readBody(req: IncomingMessage, limit = BODY_LIMIT): Promise<Record<string, unknown>> {
    const chunks: Buffer[] = [];
    let total = 0;
    for await (const chunk of req) {
      const buf = chunk as Buffer;
      total += buf.length;
      if (total > limit) throw new StudioError('PAYLOAD_TOO_LARGE', `请求体超过 ${limit} 字节`);
      chunks.push(buf);
    }
    if (!chunks.length) return {};
    const text = Buffer.concat(chunks).toString('utf8');
    try {
      const value = JSON.parse(text) as unknown;
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('必须是 JSON 对象');
      return value as Record<string, unknown>;
    } catch (error) {
      throw new StudioError('BAD_REQUEST', `请求体不是有效 JSON 对象：${(error as Error).message}`);
    }
  }

  function sendJson(res: ServerResponse, status: number, body: unknown): void {
  // 响应序列化与写出由调用方 endTiming 统一计时（见 handle 内的 phases.response）
    const text = JSON.stringify(body, null, 2);
    res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'content-length': Buffer.byteLength(text), 'cache-control': 'no-store' });
    res.end(text);
  }

  async function handle(req: IncomingMessage, res: ServerResponse): Promise<void> {
    // 请求级阶段计时（仅在设置 STUDIO_TIMING_FILE 时启用）：用于定位事务服务之外的耗时。
    const timingFile = process.env.STUDIO_TIMING_FILE;
    const timingStart = process.hrtime.bigint();
    let timingLast = timingStart;
    const phases: Record<string, number> = {};
    const mark = (phase: string): void => {
      if (!timingFile) return;
      const now = process.hrtime.bigint();
      phases[phase] = Math.round(Number(now - timingLast) / 1e6 * 100) / 100;
      timingLast = now;
    };
    const finish = (pathname: string): void => {
      if (!timingFile) return;
      try {
        appendFileSync(timingFile, `${JSON.stringify({
          kind: 'request',
          path: pathname,
          total: Math.round(Number(process.hrtime.bigint() - timingStart) / 1e6 * 100) / 100,
          phases,
        })}\n`, 'utf8');
      } catch { /* 计时失败不影响业务 */ }
    };
    const origin = req.headers.origin;
    cors(res, typeof origin === 'string' ? origin : undefined);
    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }
    checkHost(req);
    const url = new URL(req.url ?? '/', serviceUrl(options.host ?? '127.0.0.1', options.port ?? 0));
    if (timingFile) res.on('finish', () => finish(url.pathname));
    const segments = url.pathname.split('/').filter(Boolean);
    const method = req.method ?? 'GET';

    if (segments[0] !== 'v1') throw new StudioError('NOT_FOUND', `未知路径 ${url.pathname}`);

    // Only a random, expiring playback ticket permits these public reads; it grants no API access.
    if (segments[1] === 'playback') {
      playback.serve(req, res, url.pathname);
      return;
    }

    // ---- 无需认证 ----
    if (method === 'GET' && segments[1] === 'health') {
      sendJson(res, 200, {
        status: 'ok',
        service: SERVICE_NAME,
        serviceVersion: SERVICE_VERSION,
        apiVersion: API_VERSION,
        instanceId: options.instanceId,
        pid: process.pid,
        startedAt: options.startedAt,
        workspace: options.workspaceRoot,
        // 内存自述（规模测量用）：rss 为当前值，peakRssMB 为服务启动以来的峰值
        rssMB: Math.round(process.memoryUsage().rss / 1024 / 1024),
        peakRssMB: Math.round(peakRssBytes() / 1024 / 1024),
      });
      return;
    }
    if (method === 'POST' && segments[1] === 'sessions' && segments.length === 2) {
      const body = await readBody(req);
      const created = options.auth.createSession({
        pairingKey: String(body.pairingKey ?? ''),
        clientName: String(body.clientName ?? ''),
        scopes: Array.isArray(body.scopes) ? body.scopes as ActorRef['scopes'] : undefined,
        channel: body.channel === 'cli' ? 'cli' : 'mcp',
      });
      sendJson(res, 201, {
        session: { ...created.session, tokenHash: undefined },
        token: created.token,
        note: '令牌只在本次响应返回；请保存在客户端凭据存储中，不要写入工程或日志。',
      });
      return;
    }
    if (method === 'POST' && segments[1] === 'ui-session') {
      if (!origin || !uiOrigins.has(String(origin))) {
        throw new StudioError('FORBIDDEN', `人工界面来源未授权：${String(origin ?? '(无 Origin)')}`, { details: { allowedOrigins: [...uiOrigins] } });
      }
      const created = options.auth.createUiSession('local-ui');
      sendJson(res, 201, { session: { ...created.session, tokenHash: undefined }, token: created.token });
      return;
    }

    // ---- 需要认证 ----
    const actor = authenticate(req);
    mark('auth');

    if (method === 'GET' && segments[1] === 'capabilities') {
      requireScope(actor, 'read');
      sendJson(res, 200, capabilities(options));
      return;
    }
    if (segments[1] === 'sessions') {
      requireScope(actor, 'admin');
      if (method === 'GET' && segments.length === 2) {
        sendJson(res, 200, { apiVersion: API_VERSION, sessions: options.auth.listSessions() });
        return;
      }
      if (method === 'POST' && segments.length === 4 && segments[3] === 'pause') {
        sendJson(res, 200, { session: redact(options.auth.setPaused(segments[2], true)) });
        return;
      }
      if (method === 'POST' && segments.length === 4 && segments[3] === 'resume') {
        sendJson(res, 200, { session: redact(options.auth.setPaused(segments[2], false)) });
        return;
      }
      if (method === 'DELETE' && segments.length === 3) {
        sendJson(res, 200, options.auth.revoke(segments[2]));
        return;
      }
    }

    // 人工界面专用的会话管理：可以列出并暂停/恢复 AI 会话，但不能创建会话或更改作用域。
    if (segments[1] === 'ai-sessions') {
      requireScope(actor, 'edit');
      if (actor.channel !== 'human-ui') throw new StudioError('SCOPE_REQUIRED', '只有人工界面会话可以管理 AI 会话写入权限');
      if (method === 'GET' && segments.length === 2) {
        sendJson(res, 200, {
          apiVersion: API_VERSION,
          sessions: options.auth.listSessions().filter((session) => session.channel !== 'human-ui'),
        });
        return;
      }
      if (method === 'POST' && segments.length === 4 && (segments[3] === 'pause' || segments[3] === 'resume')) {
        const target = options.auth.listSessions().find((session) => session.sessionId === segments[2]);
        if (!target) throw new StudioError('NOT_FOUND', `会话 ${segments[2]} 不存在`);
        if (target.channel === 'human-ui') throw new StudioError('FORBIDDEN', '不能暂停人工界面会话');
        sendJson(res, 200, { session: redact(options.auth.setPaused(segments[2], segments[3] === 'pause')) });
        return;
      }
    }

    if (segments[1] === 'projects') {
      if (method === 'GET' && segments.length === 2) {
        requireScope(actor, 'read');
        sendJson(res, 200, { apiVersion: API_VERSION, projects: listProjects(options.workspaceRoot) });
        return;
      }
      if (method === 'POST' && segments.length === 2) {
        requireScope(actor, 'edit');
        const body = await readBody(req);
        const { createProject } = await import('../store/workspace.ts');
        const created = createProject(options.workspaceRoot, {
          title: String(body.title ?? ''),
          projectId: body.projectId ? String(body.projectId) : undefined,
          chapterTitle: body.chapterTitle ? String(body.chapterTitle) : undefined,
          sceneTitle: body.sceneTitle ? String(body.sceneTitle) : undefined,
        });
        sendJson(res, 201, created);
        return;
      }
      const projectId = segments[2];
      if (method === 'POST' && segments.length === 4 && segments[3] === 'uploads') {
        requireScope(actor, 'edit');
        if (actor.channel !== 'human-ui') throw new StudioError('FORBIDDEN', '文件上传只允许人工编辑器会话');
        const body = await readBody(req, UPLOAD_BODY_LIMIT);
        const result = applyUiUpload(options.editService, options.workspaceRoot, actor, projectId, body);
        sendJson(res, 200, result);
        return;
      }
      if (method === 'POST' && segments.length === 4 && segments[3] === 'playback') {
        requireScope(actor, 'preview');
        const body = await readBody(req);
        if (body.sceneId !== undefined && typeof body.sceneId !== 'string') throw new StudioError('BAD_REQUEST', '预览场景必须是场景 ID');
        sendJson(res, 201, playback.project(playbackOrigin(), projectId, options.editService.snapshot(projectId), body.sceneId as string | undefined));
        return;
      }
      if (method === 'GET' && segments.length === 3) {
        requireScope(actor, 'read');
        const snapshot = options.editService.snapshot(projectId);
        sendJson(res, 200, {
          apiVersion: API_VERSION,
          revision: snapshot.revision,
          project: snapshot.content.project,
          summary: summarizeProject(options.workspaceRoot, projectId),
        });
        return;
      }
      if (method === 'GET' && segments.length === 4 && segments[3] === 'outline') {
        requireScope(actor, 'read');
        const snapshot = options.editService.snapshot(projectId);
        sendJson(res, 200, { revision: snapshot.revision, outline: sceneOutline(snapshot.content), characters: snapshot.content.characters, levels: snapshot.content.levels, variables: snapshot.content.variables, theme: snapshot.content.theme ?? DEFAULT_THEME, recipes: snapshot.content.recipes ?? [] });
        return;
      }
      if (method === 'GET' && segments.length === 4 && segments[3] === 'assets') {
        requireScope(actor, 'read');
        const snapshot = options.editService.snapshot(projectId);
        sendJson(res, 200, { revision: snapshot.revision, assets: snapshot.content.assets });
        return;
      }
      if (method === 'GET' && segments.length === 6 && segments[3] === 'assets' && segments[5] === 'raw') {
        requireScope(actor, 'read');
        const snapshot = options.editService.snapshot(projectId);
        const asset = snapshot.content.assets.find((item) => item.assetId === segments[4]);
        if (!asset) throw new StudioError('NOT_FOUND', `素材 ${segments[4]} 不存在`);
        const abs = `${options.workspaceRoot}/${projectId}/${asset.path}`.replace(/\//g, process.platform === 'win32' ? '\\' : '/');
        if (!existsSync(abs)) throw new StudioError('NOT_FOUND', `素材文件不存在：${asset.path}`);
        const bytes = readBinary(abs);
        res.writeHead(200, { 'content-type': asset.mimeType, 'content-length': bytes.length, 'cache-control': 'no-store', etag: `"${asset.sha256}"` });
        res.end(bytes);
        return;
      }
      if (method === 'GET' && segments.length === 5 && segments[3] === 'scenes') {
        requireScope(actor, 'read');
        sendJson(res, 200, options.editService.sceneDetail(projectId, segments[4]));
        return;
      }
      if (method === 'GET' && segments.length === 4 && segments[3] === 'validate') {
        requireScope(actor, 'read');
        sendJson(res, 200, options.editService.validate(projectId));
        return;
      }
      if (method === 'GET' && segments.length === 4 && segments[3] === 'history') {
        requireScope(actor, 'read');
        sendJson(res, 200, { apiVersion: API_VERSION, transactions: options.editService.history(projectId) });
        return;
      }
      if (method === 'GET' && segments.length === 4 && segments[3] === 'changes') {
        requireScope(actor, 'read');
        const after = Number(url.searchParams.get('after') ?? '0');
        sendJson(res, 200, options.editService.changes(projectId, Number.isFinite(after) ? after : 0));
        return;
      }
      if (method === 'GET' && segments.length === 4 && segments[3] === 'search') {
        requireScope(actor, 'read');
        sendJson(res, 200, searchProject(options.editService, projectId, url.searchParams.get('q') ?? ''));
        return;
      }
      if (method === 'GET' && segments.length === 4 && segments[3] === 'events') {
        requireScope(actor, 'read');
        res.writeHead(200, { 'content-type': 'text/event-stream; charset=utf-8', 'cache-control': 'no-store', connection: 'keep-alive' });
        res.write(`event: ready\ndata: ${JSON.stringify({ projectId, apiVersion: API_VERSION })}\n\n`);
        const stream = { res, projectId };
        streams.add(stream);
        req.on('close', () => { streams.delete(stream); });
        return;
      }
      if (method === 'POST' && segments.length === 4 && segments[3] === 'transactions') {
        mark('route');
        requireScope(actor, 'edit');
        const body = await readBody(req);
        mark('body');
        const result = options.editService.applyTransaction(actor, {
          apiVersion: String(body.apiVersion ?? API_VERSION),
          requestId: String(body.requestId ?? ''),
          projectId,
          baseRevision: String(body.baseRevision ?? ''),
          operations: Array.isArray(body.operations) ? body.operations as { action: string; targetIds?: string[]; payload?: Record<string, unknown> }[] : [],
          summary: body.summary ? String(body.summary) : undefined,
        });
        mark('service');
        sendJson(res, 200, result);
        mark('respond');
        return;
      }
      if (method === 'GET' && segments.length === 5 && segments[3] === 'transactions') {
        requireScope(actor, 'read');
        const result = options.editService.getTransactionResult(projectId, segments[4]);
        if (!result) throw new StudioError('NOT_FOUND', `找不到 requestId ${segments[4]} 的提交结果`);
        sendJson(res, 200, result);
        return;
      }
      if (method === 'POST' && segments.length === 4 && segments[3] === 'checkpoints') {
        requireScope(actor, 'read');
        sendJson(res, 200, options.editService.checkpoint(projectId));
        return;
      }
      if (segments[3] === 'previews') {
        requireScope(actor, 'preview');
        const snapshot = () => options.editService.snapshot(projectId);
        if (method === 'POST' && segments.length === 4) {
          const body = await readBody(req);
          const current = snapshot();
          sendJson(res, 201, options.previews.start(actor.actorId, {
            content: current.content,
            revision: current.revision,
            sceneId: body.sceneId ? String(body.sceneId) : undefined,
            variables: body.variables && typeof body.variables === 'object' ? body.variables as Record<string, unknown> : undefined,
            levelSnapshot: body.levelSnapshot,
          }));
          return;
        }
        if (method === 'GET' && segments.length === 4) {
          sendJson(res, 200, { apiVersion: API_VERSION, previews: options.previews.list(projectId) });
          return;
        }
        if (method === 'GET' && segments.length === 5) {
          sendJson(res, 200, options.previews.summary(segments[4], projectId));
          return;
        }
        if (method === 'DELETE' && segments.length === 5) {
          sendJson(res, 200, options.previews.close(segments[4], projectId));
          return;
        }
        if (method === 'POST' && segments.length === 6 && segments[5] === 'actions') {
          const body = await readBody(req);
          const action = normalizePreviewAction(body);
          const current = snapshot();
          sendJson(res, 200, options.previews.action(segments[4], projectId, current.content, action, current.revision));
          return;
        }
        if (method === 'POST' && segments.length === 6 && segments[5] === 'render') {
          const artifact = options.previews.render(segments[4], projectId);
          const body = await readBody(req);
          if (body?.format === 'pixel') {
            // 像素级渲染（可选能力）：无头浏览器把同一份结构化快照渲染成 PNG。
            // 浏览器不可用时返回 RENDER_UNAVAILABLE，绝不伪造产物。
            const pixel = await renderHtmlToPng(options.workspaceRoot, {
              projectId,
              html: artifact.html,
              width: clampSize(body.width, 1280),
              height: clampSize(body.height, 720),
              label: `render-${segments[4].slice(-8)}`,
            });
            sendJson(res, 200, {
              ...artifact,
              html: undefined,
              htmlLength: artifact.html.length,
              pixel: {
                renderPath: pixel.renderPath,
                sha256: pixel.sha256,
                width: pixel.width,
                height: pixel.height,
                bytes: pixel.bytes,
                renderer: pixel.renderer,
                note: pixel.note,
              },
            });
            return;
          }
          sendJson(res, 200, { ...artifact, html: undefined, htmlLength: artifact.html.length });
          return;
        }
      }
      if (segments[3] === 'exports') {
        requireScope(actor, 'export');
        if (method === 'POST' && segments.length === 6 && segments[5] === 'open-directory') {
          if (actor.channel !== 'human-ui') throw new StudioError('FORBIDDEN', '打开文件夹只允许人工编辑器会话');
          sendJson(res, 200, await openExportDirectory(options.workspaceRoot, projectId, segments[4]));
          return;
        }
        if (method === 'POST' && segments.length === 6 && segments[5] === 'playback') {
          sendJson(res, 201, playback.exported(playbackOrigin(), projectId, segments[4]));
          return;
        }
        if (method === 'GET' && segments.length === 4) {
          sendJson(res, 200, { apiVersion: API_VERSION, exports: listExports(options.workspaceRoot, projectId) });
          return;
        }
        if (method === 'POST' && segments.length === 4) {
          const current = options.editService.snapshot(projectId);
          const result = exportProject({ workspaceRoot: options.workspaceRoot, repoRoot: options.repoRoot }, projectId, { revision: current.revision });
          sendJson(res, 201, result);
          return;
        }
      }
      if (segments[3] === 'media-jobs') {
        requireScope(actor, 'generate');
        if (method === 'GET' && segments.length === 4) {
          sendJson(res, 200, { apiVersion: API_VERSION, jobs: options.mediaJobs.listJobs(projectId), budget: options.mediaJobs.readBudget(projectId) });
          return;
        }
        if (method === 'POST' && segments.length === 4) {
          const body = await readBody(req);
          const request = normalizeMediaRequest(body, projectId);
          const created = await options.mediaJobs.createJob(actor, request);
          void options.mediaJobs.pump(projectId).catch(() => { /* 定时器会继续推进 */ });
          sendJson(res, 201, { ...created.job, replayed: created.replayed });
          return;
        }
        if (method === 'POST' && segments.length === 5 && segments[4] === 'estimate') {
          const body = await readBody(req);
          const request = normalizeMediaRequest(body, projectId);
          const estimate = await options.mediaJobs.estimate(request);
          sendJson(res, 200, { apiVersion: API_VERSION, requestId: request.requestId, ...estimate });
          return;
        }
        if (method === 'POST' && segments.length === 6 && segments[5] === 'accept') {
          const body = await readBody(req);
          const binding = body.binding && typeof body.binding === 'object' ? body.binding as Record<string, unknown> : undefined;
          const result = acceptMediaOutput(options.editService, options.mediaJobs, actor, {
            projectId,
            jobId: segments[4],
            outputId: String(body.outputId ?? ''),
            name: body.name ? String(body.name) : undefined,
            kind: body.kind ? String(body.kind) as 'background' | 'character' | 'cg' | 'audio' | 'video' : undefined,
            binding: binding ? {
              sceneId: String(binding.sceneId ?? ''),
              eventId: String(binding.eventId ?? ''),
              field: String(binding.field ?? 'assetId'),
              expectedObjectRevision: binding.expectedObjectRevision ? String(binding.expectedObjectRevision) : undefined,
            } : undefined,
          });
          sendJson(res, result.status === 'applied' ? 200 : 409, result);
          return;
        }
      }
      if (method === 'POST' && segments.length === 4 && segments[3] === 'undo') {
        requireScope(actor, 'edit');
        const body = await readBody(req);
        const result = options.editService.undo(actor, {
          requestId: String(body.requestId ?? ''),
          projectId,
          undoId: String(body.undoId ?? ''),
          baseRevision: body.baseRevision ? String(body.baseRevision) : undefined,
        });
        sendJson(res, 200, result);
        return;
      }
    }

    // 生成服务能力与任务查询（jobId 全局唯一，路径不带工程 ID）
    if (segments[1] === 'media' && segments[2] === 'providers') {
      requireScope(actor, 'generate');
      if (method === 'GET' && segments.length === 3) {
        const providers = await Promise.all(options.mediaJobs.listProviderIds().map(async (providerId) => options.mediaJobs.describeProvider(providerId)));
        sendJson(res, 200, {
          apiVersion: API_VERSION,
          providers,
          note: '当前只有明确标注的测试适配器（isTestAdapter=true）；真实图片/视频服务尚未接入。',
        });
        return;
      }
    }
    if (segments[1] === 'jobs') {
      requireScope(actor, 'generate');
      const jobId = segments[2];
      const location = options.mediaJobs.locateJob(jobId);
      if (!location) throw new StudioError('NOT_FOUND', `任务 ${jobId} 不存在`);
      if (method === 'GET' && segments.length === 3) {
        sendJson(res, 200, location.summary);
        return;
      }
      if (method === 'POST' && segments.length === 4 && segments[3] === 'cancel') {
        sendJson(res, 200, options.mediaJobs.requestCancel(location.projectId, jobId));
        void options.mediaJobs.pump(location.projectId).catch(() => { /* 定时器继续推进 */ });
        return;
      }
      if (method === 'POST' && segments.length === 4 && segments[3] === 'tick') {
        // 仅供证据与测试推进状态机（真实部署由后台定时器推进）
        const changed = await options.mediaJobs.step(location.projectId, jobId);
        sendJson(res, 200, { changed, job: options.mediaJobs.getJob(location.projectId, jobId) });
        return;
      }
    }

    throw new StudioError('NOT_FOUND', `未实现的接口：${method} ${url.pathname}`);
  }

  return {
    server,
    get port() {
      const address = server.address() as AddressInfo | null;
      return address ? address.port : 0;
    },
    get url() {
      const address = server.address() as AddressInfo | null;
      return serviceUrl(options.host ?? '127.0.0.1', address ? address.port : 0);
    },
    close: () => new Promise<void>((resolve) => {
      clearInterval(heartbeat);
      for (const stream of streams) {
        try { stream.res.end(); } catch { /* ignore */ }
      }
      streams.clear();
      playback.clear();
      server.close(() => resolve());
      server.closeAllConnections?.();
    }),
    emitChange,
  };
}

/** 生成请求由客户端提交结构，这里做严格转换与基本校验（能力校验由适配器负责）。 */
export function normalizeMediaRequest(body: Record<string, unknown>, projectId: string): MediaRequest {
  const kind = String(body.kind ?? '');
  if (!['image.text', 'image.edit', 'video.text', 'video.image'].includes(kind)) {
    throw new StudioError('BAD_REQUEST', `不支持的生成类型 ${kind || '(空)'}；允许：image.text / image.edit / video.text / video.image`);
  }
  const output = body.output && typeof body.output === 'object' ? body.output as Record<string, unknown> : {};
  const count = Number(output.count ?? 1);
  if (!Number.isInteger(count) || count < 1 || count > 8) throw new StudioError('BAD_REQUEST', 'output.count 必须是 1–8 的整数');
  const references = Array.isArray(body.references) ? body.references.map((item) => {
    const record = item && typeof item === 'object' ? item as Record<string, unknown> : {};
    return {
      assetId: String(record.assetId ?? ''),
      version: String(record.version ?? '1'),
      role: String(record.role ?? 'input') as MediaRequest['references'][number]['role'],
    };
  }) : [];
  const binding = body.binding && typeof body.binding === 'object' ? body.binding as Record<string, unknown> : undefined;
  return {
    apiVersion: '1',
    requestId: String(body.requestId ?? ''),
    projectId,
    providerId: String(body.providerId ?? ''),
    modelId: String(body.modelId ?? ''),
    kind: kind as MediaRequest['kind'],
    prompt: String(body.prompt ?? ''),
    negativePrompt: body.negativePrompt ? String(body.negativePrompt) : undefined,
    references,
    output: {
      aspectRatio: output.aspectRatio ? String(output.aspectRatio) : undefined,
      width: output.width === undefined ? undefined : Number(output.width),
      height: output.height === undefined ? undefined : Number(output.height),
      durationSeconds: output.durationSeconds === undefined ? undefined : Number(output.durationSeconds),
      fps: output.fps === undefined ? undefined : Number(output.fps),
      count,
      transparent: output.transparent === true,
      audio: output.audio === 'generated' ? 'generated' : output.audio === 'none' ? 'none' : undefined,
    },
    seed: body.seed === undefined ? undefined : Number(body.seed),
    providerOptions: body.providerOptions && typeof body.providerOptions === 'object' ? body.providerOptions as Record<string, unknown> : undefined,
    ...(typeof body.templateId === 'string' && body.templateId ? { templateId: body.templateId } : {}),
    binding: binding ? {
      objectId: String(binding.objectId ?? ''),
      field: String(binding.field ?? 'assetId'),
      expectedRevision: binding.expectedRevision ? String(binding.expectedRevision) : undefined,
      mode: binding.mode === 'autoApply' ? 'autoApply' : 'candidate',
    } : undefined,
    budgetPolicyId: body.budgetPolicyId ? String(body.budgetPolicyId) : undefined,
  };
}

function redact<T extends { tokenHash?: string }>(value: T): T {
  return { ...value, tokenHash: undefined };
}

/** 预览动作由客户端提交结构，这里做严格转换，避免把任意对象塞进运行内核。 */
export function normalizePreviewAction(body: Record<string, unknown>): RuntimeAction {
  const type = String(body.type ?? '');
  switch (type) {
    case 'advance':
      return { type: 'advance' };
    case 'choose': {
      const option = Number(body.option);
      if (!Number.isInteger(option) || option < 0) throw new StudioError('BAD_REQUEST', 'choose 需要非负整数 option');
      return { type: 'choose', option };
    }
    case 'tick': {
      const elapsedMs = Number(body.elapsedMs ?? 0);
      if (!Number.isFinite(elapsedMs) || elapsedMs < 0) throw new StudioError('BAD_REQUEST', 'tick 需要非负 elapsedMs');
      return { type: 'tick', elapsedMs };
    }
    case 'effectComplete':
      return { type: 'effectComplete', payload: body.payload };
    case 'effectFailed': {
      const error = body.error && typeof body.error === 'object' ? body.error as { code?: string; message?: string } : {};
      return { type: 'effectFailed', error };
    }
    case 'levelMessage': {
      const message = body.message;
      if (!isLevelMessage(message)) throw new StudioError('BAD_REQUEST', 'levelMessage 需要 {kind: ready|init|checkpoint|complete|error}');
      return { type: 'levelMessage', message };
    }
    default:
      throw new StudioError('BAD_REQUEST', `不支持的预览动作 ${type || '(空)'}`);
  }
}

export function capabilities(options: StudioServerOptions): Record<string, unknown> {
  const implementedEvents = Object.values(EVENT_SPECS).map((spec) => ({
    type: spec.type,
    label: spec.label,
    since: spec.since,
    runtimeSupport: spec.runtimeSupport,
    hostCapability: spec.hostCapability ?? null,
    waits: spec.waits === true,
  }));
  const actions = Object.values(ACTIONS).filter((action) => action.exposed === 'public').map((action) => ({ action: action.action, label: action.label, scopes: action.scopes }));
  return {
    apiVersion: API_VERSION,
    service: SERVICE_NAME,
    serviceVersion: SERVICE_VERSION,
    instanceId: options.instanceId,
    startedAt: options.startedAt,
    workspace: options.workspaceRoot,
    protocols: {
      http: API_VERSION,
      mcp: MCP_PROTOCOL_VERSIONS,
      level: LEVEL_PROTOCOL_VERSION,
      runtimeKernel: RUNTIME_KERNEL_VERSION,
      eventCatalog: EVENT_CATALOG_VERSION,
    },
    events: implementedEvents,
    actions,
    tools: TOOL_DEFINITIONS.map((tool) => ({ name: tool.name, title: tool.title, scopes: tool.scopes })),
    features: {
      transactions: true,
      idempotency: true,
      revisionConflict: true,
      undo: true,
      changeStream: true,
      historyQuery: true,
      assetImport: true,
      uiUpload: true,
      uiUploadMaxBytes: 24 * 1024 * 1024,
      preview: true,
      interactivePlayback: true,
      playbackReadOnlyLinks: true,
      export: true,
      exportOpenDirectory: process.platform === 'win32',
      exportZipDownload: false,
      mediaJobs: true,
      mediaProviders: ['fixture-local（测试适配器，isTestAdapter=true）'],
      levelProtocol: true,
    },
    unsupported: [
      '真实图片/视频服务接入（当前只有明确标注的测试适配器，不会访问网络或产生真实费用）',
      '真实 AI 宿主的插件加载验证（插件包已提供，见 plugin/studio-author/）',
      '浏览器直接下载导出 ZIP（当前提供作品播放与本机打开导出文件夹）',
    ],
  };
}

function searchProject(editService: EditService, projectId: string, query: string): Record<string, unknown> {
  const { content, revision } = editService.snapshot(projectId);
  const needle = query.trim().toLowerCase();
  const results: { kind: string; objectId: string; sceneId?: string; title: string; snippet: string }[] = [];
  if (needle) {
    for (const chapter of content.chapters) {
      if (chapter.title.toLowerCase().includes(needle)) results.push({ kind: 'chapter', objectId: chapter.chapterId, title: chapter.title, snippet: chapter.title });
    }
    for (const scene of content.scenes) {
      if (scene.title.toLowerCase().includes(needle)) results.push({ kind: 'scene', objectId: scene.sceneId, sceneId: scene.sceneId, title: scene.title, snippet: scene.title });
      for (const event of scene.events) {
        const text = JSON.stringify(event.params ?? {});
        if (text.toLowerCase().includes(needle)) {
          results.push({ kind: 'event', objectId: event.eventId, sceneId: scene.sceneId, title: `${event.type}`, snippet: text.slice(0, 200) });
        }
      }
    }
    for (const asset of content.assets) {
      if (asset.name.toLowerCase().includes(needle) || asset.assetId.includes(needle)) results.push({ kind: 'asset', objectId: asset.assetId, title: asset.name, snippet: `${asset.kind} ${asset.path}` });
    }
    for (const character of content.characters) {
      if (character.name.toLowerCase().includes(needle)) results.push({ kind: 'character', objectId: character.characterId, title: character.name, snippet: character.description ?? '' });
    }
  }
  return { apiVersion: API_VERSION, projectId, revision, query, results: results.slice(0, 100), truncated: results.length > 100 };
}

/** 渲染尺寸限制：避免误传超大画布把机器拖死。 */
function clampSize(value: unknown, fallback: number): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.min(2560, Math.max(160, Math.round(parsed)));
}

/** 后台进程的内存峰值：由 main.ts 的采样器更新（提交后与定时采样取最大）。 */
let memoryPeak = { rss: 0, heapUsed: 0 };
export function noteMemorySample(sample: { rss: number; heapUsed: number }): void {
  if (sample.rss > memoryPeak.rss) memoryPeak = probeMemory(sample);
}
function probeMemory(sample: { rss: number; heapUsed: number }): { rss: number; heapUsed: number } {
  return { rss: sample.rss, heapUsed: sample.heapUsed };
}
export function peakRssBytes(): number {
  return Math.max(memoryPeak.rss, process.memoryUsage().rss);
}
export function peakMemory(): { rss: number; heapUsed: number } {
  return { rss: peakRssBytes(), heapUsed: memoryPeak.heapUsed };
}
