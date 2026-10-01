// 预览会话服务：AI 与人工都可以在没有编辑器窗口的情况下运行工程内容。
// 使用与展示器相同的运行内核（src/runtime/kernel.ts），不模拟界面点击。
import { existsSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { RuntimeAction, RuntimeState, LevelMessage } from '../../../src/runtime/kernel.ts';
import { applyRuntimeAction, startRuntime } from '../../../src/runtime/kernel.ts';
import type { StoryContent } from '../../../src/runtime/content.ts';
import { StudioError } from '../core/errors.ts';
import { sha256Hex } from '../core/canonical.ts';
import { makeId } from '../core/ids.ts';
import { ensureDir, writeJsonDurable } from '../store/fsx.ts';
import { projectPaths } from '../store/paths.ts';
import { API_VERSION, RUNTIME_KERNEL_VERSION } from '../version.ts';

export interface PreviewSession {
  previewId: string;
  projectId: string;
  revision: string;
  actorId: string;
  createdAt: string;
  state: RuntimeState;
  renderCount: number;
}

export interface PreviewSummary {
  previewId: string;
  projectId: string;
  revision: string;
  status: RuntimeState['status'];
  sceneId: string;
  lineCount: number;
  pendingChoice: RuntimeState['pendingChoice'];
  pendingEffect: RuntimeState['pendingEffect'];
  ended: RuntimeState['ended'];
  errors: RuntimeState['errors'];
}

export interface RenderArtifact {
  previewId: string;
  revision: string;
  renderPath: string;
  sha256: string;
  html: string;
  note: string;
}

export class PreviewService {
  private sessions = new Map<string, PreviewSession>();
  private options: { workspaceRoot: string; now?: () => string };

  constructor(options: { workspaceRoot: string; now?: () => string }) {
    this.options = options;
  }

  private now(): string {
    return this.options.now ? this.options.now() : new Date().toISOString();
  }

  start(actorId: string, input: { content: StoryContent; revision: string; sceneId?: string; variables?: Record<string, unknown>; levelSnapshot?: unknown }): PreviewSummary {
    const sceneId = input.sceneId || input.content.project.entrySceneId;
    if (!input.content.scenes.some((scene) => scene.sceneId === sceneId)) {
      throw new StudioError('NOT_FOUND', `起始场景 ${sceneId} 不存在`, { issues: [{ code: 'SCENE_NOT_FOUND', message: `工程中没有场景 ${sceneId}` }] });
    }
    const state = startRuntime(input.content, sceneId, { revision: input.revision, variables: input.variables, levelSnapshot: input.levelSnapshot });
    const session: PreviewSession = {
      previewId: makeId('pvw'),
      projectId: input.content.project.projectId,
      revision: input.revision,
      actorId,
      createdAt: this.now(),
      state,
      renderCount: 0,
    };
    this.sessions.set(session.previewId, session);
    return summarize(session);
  }

  require(previewId: string, projectId: string): PreviewSession {
    const session = this.sessions.get(previewId);
    if (!session) throw new StudioError('NOT_FOUND', `预览会话 ${previewId} 不存在（预览不跨进程保留）`);
    if (session.projectId !== projectId) throw new StudioError('FORBIDDEN', `预览会话 ${previewId} 属于其它工程`);
    return session;
  }

  action(previewId: string, projectId: string, content: StoryContent, action: RuntimeAction, currentRevision: string): PreviewSummary {
    const session = this.require(previewId, projectId);
    if (session.revision !== currentRevision) {
      throw new StudioError('CONFLICT', `预览会话基于修订 ${session.revision}，当前工程修订为 ${currentRevision}；请重新开始预览`, {
        details: { previewRevision: session.revision, currentRevision },
      });
    }
    session.state = applyRuntimeAction(content, session.state, action);
    return summarize(session);
  }

  summary(previewId: string, projectId: string): PreviewSummary {
    return summarize(this.require(previewId, projectId));
  }

  close(previewId: string, projectId: string): { closed: boolean } {
    this.require(previewId, projectId);
    this.sessions.delete(previewId);
    return { closed: true };
  }

  list(projectId: string): PreviewSummary[] {
    return [...this.sessions.values()].filter((session) => session.projectId === projectId).map(summarize);
  }

  /**
   * 生成结构化舞台快照（HTML/JSON 文本），写入 .studio/previews/。
   * 这是结构快照而不是像素级渲染：真实画面由展示器页面呈现，两者使用同一内核状态。
   */
  render(previewId: string, projectId: string): RenderArtifact {
    const session = this.require(previewId, projectId);
    const state = session.state;
    const paths = projectPaths(this.options.workspaceRoot, projectId);
    const dir = path.join(paths.studioDir, 'previews');
    ensureDir(dir);
    session.renderCount += 1;
    const name = `${previewId}-${String(session.renderCount).padStart(3, '0')}.html`;
    const html = renderSnapshotHtml(state);
    const abs = path.join(dir, name);
    writeJsonDurable(abs.replace(/\.html$/, '.json'), {
      apiVersion: API_VERSION,
      kernelVersion: RUNTIME_KERNEL_VERSION,
      previewId,
      revision: session.revision,
      status: state.status,
      sceneId: state.sceneId,
      stage: state.stage,
      line: state.lines[state.lines.length - 1] ?? null,
      pendingChoice: state.pendingChoice,
      pendingEffect: state.pendingEffect,
      variables: state.variables,
      errors: state.errors,
      note: '结构化运行状态快照，不是像素级渲染结果。',
    }, 'render');
    writeFileSync(abs, html, 'utf8');
    return {
      previewId,
      revision: session.revision,
      renderPath: path.relative(paths.root, abs).replace(/\\/g, '/'),
      sha256: sha256Hex(html),
      html,
      note: '结构化舞台快照（背景/人物/对白/选项的结构与文本），不是像素级渲染截图。',
    };
  }
}

function summarize(session: PreviewSession): PreviewSummary {
  return {
    previewId: session.previewId,
    projectId: session.projectId,
    revision: session.revision,
    status: session.state.status,
    sceneId: session.state.sceneId,
    lineCount: session.state.lines.length,
    pendingChoice: session.state.pendingChoice,
    pendingEffect: session.state.pendingEffect,
    ended: session.state.ended,
    errors: session.state.errors,
  };
}

export function renderSnapshotHtml(state: RuntimeState): string {
  const escape = (value: string): string => value.replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char] as string));
  const line = state.lines[state.lines.length - 1];
  const characters = state.stage.characters.map((character) => `<div class="character ${escape(character.position)}">${escape(character.assetId)}</div>`).join('\n      ');
  const choice = state.pendingChoice
    ? `<ol class="choices">${state.pendingChoice.options.map((option) => `<li>${escape(option.label)}</li>`).join('')}</ol>`
    : '';
  const status = `status=${state.status} scene=${state.sceneId} revision=${state.revision}`;
  return `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8" /><title>预览快照 ${escape(state.sceneId)}</title>
<style>
  body { margin: 0; font: 14px/1.6 "Segoe UI", "Microsoft YaHei", sans-serif; background: #10131a; color: #e6e9ef; }
  .stage { position: relative; height: 420px; border: 1px solid #2c3340; border-radius: 10px; margin: 12px; overflow: hidden; }
  .background { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: #9aa4b2; background: #171b22; }
  .character { position: absolute; bottom: 60px; padding: 24px 16px; border: 1px dashed #4a5464; border-radius: 8px; color: #cfe0ff; }
  .character.left { left: 12%; } .character.center { left: 44%; } .character.right { right: 12%; }
  .dialogue { position: absolute; left: 12px; right: 12px; bottom: 12px; background: rgba(12,14,20,.86); border: 1px solid #2c3340; border-radius: 8px; padding: 10px 12px; }
  .speaker { color: #6ea8fe; }
  .meta { margin: 0 12px; color: #9aa4b2; font-size: 12px; }
  .choices { margin: 8px 12px; color: #4ec9a0; }
</style></head>
<body>
  <p class="meta">结构化舞台快照：${escape(status)}（不是像素级渲染结果）</p>
  <div class="stage">
    <div class="background">背景：${escape(state.stage.background ?? '（未设置）')}</div>
    ${characters}
    <div class="dialogue"><span class="speaker">${escape(line?.speaker || '旁白')}</span>：${escape(line?.text ?? '（当前没有对白）')}</div>
  </div>
  ${choice}
</body></html>
`;
}

export function isLevelMessage(value: unknown): value is LevelMessage {
  if (!value || typeof value !== 'object') return false;
  const kind = (value as { kind?: unknown }).kind;
  return typeof kind === 'string' && ['ready', 'init', 'checkpoint', 'complete', 'error'].includes(kind);
}

export function previewArtifactsExist(workspaceRoot: string, projectId: string, relative: string): boolean {
  return existsSync(path.join(projectPaths(workspaceRoot, projectId).root, relative));
}
