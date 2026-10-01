// 编辑动作注册表：人工界面与 AI 工具提交的事务最终都由这里执行同一套编辑规则。
// 每个动作负责：校验载荷、改写内存内容、计算 changedIds、给出补偿操作（撤销用）与摘要。
import { DEFAULT_THEME } from '../../../src/runtime/content.ts';
import type { RecipeDoc, ThemeDoc, AssetDoc, ChapterDoc, EventRecord, LevelDoc, SceneDoc, StoryContent, VariableDoc } from '../../../src/runtime/content.ts';
import { eventSummary, isEventType, validateEventParams, EVENT_SPECS } from '../../../src/runtime/events.ts';
import type { EventType } from '../../../src/runtime/events.ts';
import { ASSET_KINDS } from '../../../src/runtime/content.ts';
import type { FileChange } from '../store/doc.ts';
import type { OperationPayload } from '../store/journal.ts';
import { StudioError } from '../core/errors.ts';
import { ID_PREFIX, isStudioId, makeId } from '../core/ids.ts';
import { decodeAsset, guessMimeType } from '../media/decode.ts';

export interface ActionIO {
  now: string;
  /** 读取已授权导入根下的文件；未授权路径必须抛 FORBIDDEN。 */
  readAuthorizedFile(sourcePath: string): { absPath: string; bytes: Buffer };
}

export interface ApplyResult {
  changedIds: string[];
  summary: string;
  inverse: OperationPayload[];
  binaryFiles?: FileChange[];
}

export interface ActionDefinition {
  action: string;
  label: string;
  exposed: 'public' | 'internal';
  scopes: string[];
  targetIds(payload: Record<string, unknown>): string[];
  apply(content: StoryContent, payload: Record<string, unknown>, io: ActionIO): ApplyResult;
}

function fail(message: string, issues?: { code: string; message: string; field?: string; objectId?: string }[]): never {
  throw new StudioError('VALIDATION_FAILED', message, { issues: issues ?? [] });
}

function asRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`${label} 必须是对象`);
  return value as Record<string, unknown>;
}

function str(payload: Record<string, unknown>, key: string, options: { label?: string; required?: boolean; maxLength?: number; pattern?: RegExp; default?: string } = {}): string {
  const label = options.label ?? key;
  const value = payload[key];
  if (value === undefined || value === null || value === '') {
    if (options.required) fail(`${label}不能为空`);
    return options.default ?? '';
  }
  if (typeof value !== 'string') fail(`${label}必须是字符串`);
  if (options.maxLength && value.length > options.maxLength) fail(`${label}最长 ${options.maxLength} 字`);
  if (options.pattern && !options.pattern.test(value)) fail(`${label}格式不正确：${value}`);
  return value;
}

function num(payload: Record<string, unknown>, key: string, options: { label?: string; required?: boolean; min?: number; max?: number; integer?: boolean; default?: number } = {}): number {
  const label = options.label ?? key;
  const value = payload[key];
  if (value === undefined || value === null || value === '') {
    if (options.required) fail(`${label}不能为空`);
    return options.default ?? 0;
  }
  if (typeof value !== 'number' || Number.isNaN(value)) fail(`${label}必须是数字`);
  if (options.integer && !Number.isInteger(value)) fail(`${label}必须是整数`);
  if (options.min !== undefined && value < options.min) fail(`${label}不能小于 ${options.min}`);
  if (options.max !== undefined && value > options.max) fail(`${label}不能大于 ${options.max}`);
  return value;
}

function idOrNew(payload: Record<string, unknown>, key: string, prefix: string, label: string): string {
  const value = payload[key];
  if (value === undefined || value === null || value === '') return makeId(prefix);
  if (typeof value !== 'string' || !isStudioId(value)) fail(`${label}必须是稳定 ID（形如 ${prefix}_xxx）`);
  if (!value.startsWith(`${prefix}_`)) fail(`${label}前缀必须是 ${prefix}_`);
  return value;
}

function requireScene(content: StoryContent, sceneId: string): SceneDoc {
  const scene = content.scenes.find((item) => item.sceneId === sceneId);
  if (!scene) fail(`场景 ${sceneId} 不存在`);
  return scene;
}

function requireChapter(content: StoryContent, chapterId: string): ChapterDoc {
  const chapter = content.chapters.find((item) => item.chapterId === chapterId);
  if (!chapter) fail(`章节 ${chapterId} 不存在`);
  return chapter;
}

function requireEvent(content: StoryContent, sceneId: string, eventId: string): { scene: SceneDoc; index: number; event: EventRecord } {
  const scene = requireScene(content, sceneId);
  const index = scene.events.findIndex((item) => item.eventId === eventId);
  if (index < 0) fail(`事件 ${eventId} 不存在于场景 ${sceneId}`);
  return { scene, index, event: scene.events[index] };
}

function eventValidationContext(content: StoryContent) {
  const assets: Record<string, AssetDoc['kind']> = {};
  for (const asset of content.assets) assets[asset.assetId] = asset.kind;
  const eventIds: Record<string, string[]> = {};
  for (const scene of content.scenes) eventIds[scene.sceneId] = scene.events.map((event) => event.eventId);
  return {
    assets,
    sceneIds: content.scenes.map((scene) => scene.sceneId),
    eventIds,
    variableIds: content.variables.map((variable) => variable.variableId),
    levelIds: content.levels.map((level) => level.levelId),
    characterIds: content.characters.map((character) => character.characterId),
  };
}

function normalizeParams(type: EventType, params: unknown, content: StoryContent): Record<string, unknown> {
  const value = asRecord(params ?? {}, '事件参数');
  const issues = validateEventParams(type, value, eventValidationContext(content));
  if (issues.length) fail(`事件参数不合法（${type}）`, issues);
  return value;
}

export const ACTIONS: Record<string, ActionDefinition> = {
  'project.update': {
    action: 'project.update', label: '修改工程信息', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => [typeof payload.projectId === 'string' ? payload.projectId : ''],
    apply(content, payload, io) {
      const inversePayload: Record<string, unknown> = { projectId: content.project.projectId };
      const changedIds: string[] = [];
      if (payload.title !== undefined) {
        const title = str(payload, 'title', { label: '工程标题', required: true, maxLength: 120 });
        inversePayload.title = content.project.title;
        content.project.title = title;
        changedIds.push(content.project.projectId);
      }
      if (payload.entrySceneId !== undefined) {
        const entrySceneId = str(payload, 'entrySceneId', { label: '入口场景', required: true });
        if (!content.scenes.some((scene) => scene.sceneId === entrySceneId)) fail(`入口场景 ${entrySceneId} 不存在`);
        inversePayload.entrySceneId = content.project.entrySceneId;
        content.project.entrySceneId = entrySceneId;
        changedIds.push(content.project.projectId);
      }
      if (!changedIds.length) fail('没有需要修改的字段');
      content.project.updatedAt = io.now;
      return { changedIds, summary: `更新工程信息（${changedIds.length} 个字段）`, inverse: [{ action: 'project.update', targetIds: [content.project.projectId], payload: inversePayload }] };
    },
  },

  'chapter.create': {
    action: 'chapter.create', label: '创建章节', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.chapterId === 'string' && payload.chapterId ? [payload.chapterId] : []),
    apply(content, payload, io) {
      const chapterId = idOrNew(payload, 'chapterId', ID_PREFIX.chapter, '章节 ID');
      if (content.chapters.some((chapter) => chapter.chapterId === chapterId)) fail(`章节 ${chapterId} 已存在`);
      const title = str(payload, 'title', { label: '章节标题', required: true, maxLength: 120 });
      const order = payload.order === undefined ? content.chapters.length : num(payload, 'order', { label: '排序', integer: true, min: 0, max: 9999 });
      const chapter: ChapterDoc = { chapterId, title, order, sceneIds: [], updatedAt: io.now };
      content.chapters.push(chapter);
      content.chapters.sort((a, b) => a.order - b.order);
      content.project.chapterIds = content.chapters.map((item) => item.chapterId);
      content.project.updatedAt = io.now;
      return {
        changedIds: [chapterId],
        summary: `创建章节《${title}》`,
        inverse: [{ action: 'chapter.delete', targetIds: [chapterId], payload: { chapterId } }],
      };
    },
  },

  'chapter.update': {
    action: 'chapter.update', label: '修改章节', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.chapterId === 'string' ? [payload.chapterId] : []),
    apply(content, payload, io) {
      const chapterId = str(payload, 'chapterId', { label: '章节 ID', required: true });
      const chapter = requireChapter(content, chapterId);
      const inversePayload: Record<string, unknown> = { chapterId };
      const changed: string[] = [];
      if (payload.title !== undefined) {
        const title = str(payload, 'title', { label: '章节标题', required: true, maxLength: 120 });
        inversePayload.title = chapter.title;
        chapter.title = title;
        changed.push('title');
      }
      if (payload.order !== undefined) {
        inversePayload.order = chapter.order;
        chapter.order = num(payload, 'order', { label: '排序', required: true, integer: true, min: 0, max: 9999 });
        changed.push('order');
      }
      if (!changed.length) fail('没有需要修改的字段');
      chapter.updatedAt = io.now;
      content.chapters.sort((a, b) => a.order - b.order);
      content.project.chapterIds = content.chapters.map((item) => item.chapterId);
      content.project.updatedAt = io.now;
      return {
        changedIds: [chapterId],
        summary: `修改章节《${chapter.title}》`,
        inverse: [{ action: 'chapter.update', targetIds: [chapterId], payload: inversePayload }],
      };
    },
  },

  'chapter.delete': {
    action: 'chapter.delete', label: '删除章节', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.chapterId === 'string' ? [payload.chapterId] : []),
    apply(content, payload, io) {
      const chapterId = str(payload, 'chapterId', { label: '章节 ID', required: true });
      const chapter = requireChapter(content, chapterId);
      if (chapter.sceneIds.length) fail(`章节《${chapter.title}》还有 ${chapter.sceneIds.length} 个场景，请先移动或删除场景`);
      if (content.chapters.length <= 1) fail('工程至少需要保留一个章节');
      content.chapters = content.chapters.filter((item) => item.chapterId !== chapterId);
      content.project.chapterIds = content.chapters.map((item) => item.chapterId);
      content.project.updatedAt = io.now;
      return {
        changedIds: [chapterId],
        summary: `删除章节《${chapter.title}》`,
        inverse: [{ action: 'chapter.restore', targetIds: [chapterId], payload: { snapshot: chapter } }],
      };
    },
  },

  'chapter.restore': {
    action: 'chapter.restore', label: '恢复章节（撤销用）', exposed: 'internal', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.snapshot === 'object' && payload.snapshot ? [(payload.snapshot as ChapterDoc).chapterId] : []),
    apply(content, payload, io) {
      const snapshot = asRecord(payload.snapshot, '章节快照') as unknown as ChapterDoc;
      if (content.chapters.some((chapter) => chapter.chapterId === snapshot.chapterId)) fail(`章节 ${snapshot.chapterId} 已存在`);
      const chapter: ChapterDoc = { ...snapshot, updatedAt: io.now };
      content.chapters.push(chapter);
      content.chapters.sort((a, b) => a.order - b.order);
      content.project.chapterIds = content.chapters.map((item) => item.chapterId);
      content.project.updatedAt = io.now;
      return { changedIds: [chapter.chapterId], summary: `恢复章节《${chapter.title}》`, inverse: [{ action: 'chapter.delete', targetIds: [chapter.chapterId], payload: { chapterId: chapter.chapterId } }] };
    },
  },

  'scene.create': {
    action: 'scene.create', label: '创建场景', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.sceneId === 'string' && payload.sceneId ? [payload.sceneId] : []),
    apply(content, payload, io) {
      const chapterId = str(payload, 'chapterId', { label: '章节 ID', required: true });
      const chapter = requireChapter(content, chapterId);
      const sceneId = idOrNew(payload, 'sceneId', ID_PREFIX.scene, '场景 ID');
      if (content.scenes.some((scene) => scene.sceneId === sceneId)) fail(`场景 ${sceneId} 已存在`);
      const title = str(payload, 'title', { label: '场景标题', required: true, maxLength: 120 });
      const scene: SceneDoc = { sceneId, chapterId, title, events: [], updatedAt: io.now };
      content.scenes.push(scene);
      const index = payload.index === undefined ? chapter.sceneIds.length : num(payload, 'index', { label: '位置', integer: true, min: 0 });
      chapter.sceneIds.splice(Math.min(index, chapter.sceneIds.length), 0, sceneId);
      chapter.updatedAt = io.now;
      if (!content.project.entrySceneId) content.project.entrySceneId = sceneId;
      content.project.updatedAt = io.now;
      return {
        changedIds: [sceneId, chapterId],
        summary: `在《${chapter.title}》创建场景《${title}》`,
        inverse: [{ action: 'scene.delete', targetIds: [sceneId], payload: { sceneId } }],
      };
    },
  },

  'scene.update': {
    action: 'scene.update', label: '修改场景', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.sceneId === 'string' ? [payload.sceneId] : []),
    apply(content, payload, io) {
      const sceneId = str(payload, 'sceneId', { label: '场景 ID', required: true });
      const scene = requireScene(content, sceneId);
      const title = str(payload, 'title', { label: '场景标题', required: true, maxLength: 120 });
      const previous = scene.title;
      scene.title = title;
      scene.updatedAt = io.now;
      content.project.updatedAt = io.now;
      return {
        changedIds: [sceneId],
        summary: `场景改名为《${title}》`,
        inverse: [{ action: 'scene.update', targetIds: [sceneId], payload: { sceneId, title: previous } }],
      };
    },
  },

  'scene.delete': {
    action: 'scene.delete', label: '删除场景', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.sceneId === 'string' ? [payload.sceneId] : []),
    apply(content, payload, io) {
      const sceneId = str(payload, 'sceneId', { label: '场景 ID', required: true });
      const scene = requireScene(content, sceneId);
      if (content.scenes.length <= 1) fail('工程至少需要保留一个场景');
      if (content.project.entrySceneId === sceneId) fail('入口场景不能删除，请先修改入口场景');
      const chapterIndex = requireChapter(content, scene.chapterId).sceneIds.indexOf(sceneId);
      const contentIndex = content.scenes.findIndex((item) => item.sceneId === sceneId);
      content.scenes = content.scenes.filter((item) => item.sceneId !== sceneId);
      const chapter = requireChapter(content, scene.chapterId);
      chapter.sceneIds = chapter.sceneIds.filter((id) => id !== sceneId);
      chapter.updatedAt = io.now;
      content.project.updatedAt = io.now;
      return {
        changedIds: [sceneId, scene.chapterId],
        summary: `删除场景《${scene.title}》`,
        inverse: [{ action: 'scene.restore', targetIds: [sceneId], payload: { snapshot: scene, chapterIndex, contentIndex } }],
      };
    },
  },

  'scene.restore': {
    action: 'scene.restore', label: '恢复场景（撤销用）', exposed: 'internal', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.snapshot === 'object' && payload.snapshot ? [(payload.snapshot as SceneDoc).sceneId] : []),
    apply(content, payload, io) {
      const snapshot = asRecord(payload.snapshot, '场景快照') as unknown as SceneDoc;
      if (content.scenes.some((scene) => scene.sceneId === snapshot.sceneId)) fail(`场景 ${snapshot.sceneId} 已存在`);
      const chapter = requireChapter(content, snapshot.chapterId);
      const scene: SceneDoc = { ...snapshot, updatedAt: io.now };
      const contentIndex = Number.isInteger(payload.contentIndex) ? Math.max(0, Math.min(Number(payload.contentIndex), content.scenes.length)) : content.scenes.length;
      const chapterIndex = Number.isInteger(payload.chapterIndex) ? Math.max(0, Math.min(Number(payload.chapterIndex), chapter.sceneIds.length)) : chapter.sceneIds.length;
      content.scenes.splice(contentIndex, 0, scene);
      if (!chapter.sceneIds.includes(scene.sceneId)) chapter.sceneIds.splice(chapterIndex, 0, scene.sceneId);
      chapter.updatedAt = io.now;
      content.project.updatedAt = io.now;
      return { changedIds: [scene.sceneId, chapter.chapterId], summary: `恢复场景《${scene.title}》`, inverse: [{ action: 'scene.delete', targetIds: [scene.sceneId], payload: { sceneId: scene.sceneId } }] };
    },
  },

  'scene.move': {
    action: 'scene.move', label: '移动场景', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.sceneId === 'string' ? [payload.sceneId] : []),
    apply(content, payload, io) {
      const sceneId = str(payload, 'sceneId', { label: '场景 ID', required: true });
      const scene = requireScene(content, sceneId);
      const targetChapterId = str(payload, 'chapterId', { label: '目标章节', required: true });
      const targetChapter = requireChapter(content, targetChapterId);
      const fromChapter = requireChapter(content, scene.chapterId);
      const fromIndex = fromChapter.sceneIds.indexOf(sceneId);
      const index = payload.index === undefined ? targetChapter.sceneIds.length : num(payload, 'index', { label: '位置', integer: true, min: 0 });
      fromChapter.sceneIds = fromChapter.sceneIds.filter((id) => id !== sceneId);
      targetChapter.sceneIds.splice(Math.min(index, targetChapter.sceneIds.length), 0, sceneId);
      scene.chapterId = targetChapterId;
      scene.updatedAt = io.now;
      fromChapter.updatedAt = io.now;
      targetChapter.updatedAt = io.now;
      content.project.updatedAt = io.now;
      return {
        changedIds: [sceneId, fromChapter.chapterId, targetChapter.chapterId],
        summary: `移动场景《${scene.title}》到《${targetChapter.title}》`,
        inverse: [{ action: 'scene.move', targetIds: [sceneId], payload: { sceneId, chapterId: fromChapter.chapterId, index: fromIndex } }],
      };
    },
  },

  'event.insert': {
    action: 'event.insert', label: '插入事件', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.eventId === 'string' && payload.eventId ? [payload.eventId] : []),
    apply(content, payload, io) {
      const sceneId = str(payload, 'sceneId', { label: '场景 ID', required: true });
      const scene = requireScene(content, sceneId);
      const type = str(payload, 'type', { label: '事件类型', required: true });
      if (!isEventType(type)) fail(`不支持的事件类型 ${type}`);
      const params = normalizeParams(type, payload.params, content);
      const eventId = idOrNew(payload, 'eventId', ID_PREFIX.event, '事件 ID');
      if (scene.events.some((event) => event.eventId === eventId)) fail(`事件 ${eventId} 已存在`);
      const index = payload.index === undefined ? scene.events.length : num(payload, 'index', { label: '位置', integer: true, min: 0 });
      const event: EventRecord = { eventId, type, params, createdAt: io.now, updatedAt: io.now };
      scene.events.splice(Math.min(index, scene.events.length), 0, event);
      scene.updatedAt = io.now;
      content.project.updatedAt = io.now;
      return {
        changedIds: [sceneId, eventId],
        summary: `在《${scene.title}》插入事件 ${eventId}：${eventSummary(type, params)}`,
        inverse: [{ action: 'event.delete', targetIds: [eventId], payload: { sceneId, eventId } }],
      };
    },
  },

  'event.update': {
    action: 'event.update', label: '修改事件', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.eventId === 'string' ? [payload.eventId] : []),
    apply(content, payload, io) {
      const sceneId = str(payload, 'sceneId', { label: '场景 ID', required: true });
      const eventId = str(payload, 'eventId', { label: '事件 ID', required: true });
      const { event, scene } = requireEvent(content, sceneId, eventId);
      if (payload.type !== undefined && payload.type !== event.type) fail('事件类型不可修改，请删除后重新插入');
      const params = normalizeParams(event.type, payload.params, content);
      const previous = event.params;
      event.params = params;
      event.updatedAt = io.now;
      scene.updatedAt = io.now;
      content.project.updatedAt = io.now;
      return {
        changedIds: [sceneId, eventId],
        summary: `修改事件 ${eventId}：${eventSummary(event.type, params)}`,
        inverse: [{ action: 'event.update', targetIds: [eventId], payload: { sceneId, eventId, params: previous } }],
      };
    },
  },

  'event.delete': {
    action: 'event.delete', label: '删除事件', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.eventId === 'string' ? [payload.eventId] : []),
    apply(content, payload, io) {
      const sceneId = str(payload, 'sceneId', { label: '场景 ID', required: true });
      const eventId = str(payload, 'eventId', { label: '事件 ID', required: true });
      const { scene, index, event } = requireEvent(content, sceneId, eventId);
      scene.events.splice(index, 1);
      scene.updatedAt = io.now;
      content.project.updatedAt = io.now;
      return {
        changedIds: [sceneId, eventId],
        summary: `删除事件 ${eventId}（${eventSummary(event.type, event.params)}）`,
        inverse: [{ action: 'event.insert', targetIds: [eventId], payload: { sceneId, index, eventId, type: event.type, params: event.params } }],
      };
    },
  },

  'event.move': {
    action: 'event.move', label: '移动事件', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.eventId === 'string' ? [payload.eventId] : []),
    apply(content, payload, io) {
      const sceneId = str(payload, 'sceneId', { label: '场景 ID', required: true });
      const eventId = str(payload, 'eventId', { label: '事件 ID', required: true });
      const { scene, index, event } = requireEvent(content, sceneId, eventId);
      const target = num(payload, 'index', { label: '目标位置', required: true, integer: true, min: 0, max: scene.events.length });
      scene.events.splice(index, 1);
      scene.events.splice(Math.min(target, scene.events.length), 0, event);
      scene.updatedAt = io.now;
      content.project.updatedAt = io.now;
      return {
        changedIds: [sceneId, eventId],
        summary: `调整事件 ${eventId} 到第 ${target + 1} 位`,
        inverse: [{ action: 'event.move', targetIds: [eventId], payload: { sceneId, eventId, index } }],
      };
    },
  },

    'recipe.create': {
      action: 'recipe.create', label: '新建生成模板', exposed: 'public', scopes: ['edit', 'generate'],
      targetIds: (payload) => (typeof payload.recipeId === 'string' && payload.recipeId ? [payload.recipeId] : []),
      apply(content, payload, io) {
        if (payload.recipeRestore !== undefined) {
          const restore = asRecord(payload.recipeRestore, '模板快照') as unknown as RecipeDoc;
          content.recipes = [...(content.recipes ?? []).filter((item) => item.recipeId !== restore.recipeId), restore];
          content.project.updatedAt = io.now;
          return { changedIds: [restore.recipeId], summary: '恢复生成模板', inverse: [] };
        }
        const recipeId = idOrNew(payload, 'recipeId', 'rcp', '生成模板 ID');
        if ((content.recipes ?? []).some((item) => item.recipeId === recipeId)) fail(`生成模板 ${recipeId} 已存在`);
        const name = str(payload, 'name', { label: '模板名', required: true, maxLength: 60 });
        const taskType = str(payload, 'taskType', { label: '任务类型', required: true, pattern: /^(image\.text|image\.edit|video\.text|video\.image)$/ }) as RecipeDoc['taskType'];
        const prompt = str(payload, 'prompt', { label: '提示词', required: true, maxLength: 2000 });
        const negativePrompt = payload.negativePrompt === undefined ? '' : str(payload, 'negativePrompt', { label: '负向提示词', maxLength: 1000 });
        const params = payload.params && typeof payload.params === 'object' ? payload.params as Record<string, unknown> : {};
        const recipe: RecipeDoc = { recipeId, name, taskType, prompt, ...(negativePrompt ? { negativePrompt } : {}), params, updatedAt: io.now };
        content.recipes = [...(content.recipes ?? []), recipe];
        content.project.updatedAt = io.now;
        return { changedIds: [recipeId], summary: `新建生成模板 ${name}`, inverse: [{ action: 'recipe.delete', targetIds: [recipeId], payload: { recipeId } }] };
      },
    },
    'recipe.update': {
      action: 'recipe.update', label: '修改生成模板', exposed: 'public', scopes: ['edit', 'generate'],
      targetIds: (payload) => (typeof payload.recipeId === 'string' ? [payload.recipeId] : []),
      apply(content, payload, io) {
        const recipeId = str(payload, 'recipeId', { label: '生成模板 ID', required: true });
        const recipe = (content.recipes ?? []).find((item) => item.recipeId === recipeId);
        if (!recipe) fail(`生成模板 ${recipeId} 不存在`);
        const previous: RecipeDoc = { ...recipe };
        if (payload.name !== undefined) recipe.name = str(payload, 'name', { label: '模板名', required: true, maxLength: 60 });
        if (payload.taskType !== undefined) recipe.taskType = str(payload, 'taskType', { label: '任务类型', required: true, pattern: /^(image\.text|image\.edit|video\.text|video\.image)$/ }) as RecipeDoc['taskType'];
        if (payload.prompt !== undefined) recipe.prompt = str(payload, 'prompt', { label: '提示词', required: true, maxLength: 2000 });
        if (payload.negativePrompt !== undefined) recipe.negativePrompt = str(payload, 'negativePrompt', { label: '负向提示词', maxLength: 1000 });
        if (payload.params !== undefined) recipe.params = payload.params as Record<string, unknown>;
        recipe.updatedAt = io.now;
        content.project.updatedAt = io.now;
        return { changedIds: [recipeId], summary: `修改生成模板 ${recipe.name}`, inverse: [{ action: 'recipe.update', targetIds: [recipeId], payload: { recipeRestore: previous } }] };
      },
    },
    'recipe.delete': {
      action: 'recipe.delete', label: '删除生成模板', exposed: 'public', scopes: ['edit', 'generate'],
      targetIds: (payload) => (typeof payload.recipeId === 'string' ? [payload.recipeId] : []),
      apply(content, payload) {
        const recipeId = str(payload, 'recipeId', { label: '生成模板 ID', required: true });
        const recipe = (content.recipes ?? []).find((item) => item.recipeId === recipeId);
        if (!recipe) fail(`生成模板 ${recipeId} 不存在`);
        content.recipes = (content.recipes ?? []).filter((item) => item.recipeId !== recipeId);
        return { changedIds: [recipeId], summary: `删除生成模板 ${recipe.name}`, inverse: [{ action: 'recipe.create', targetIds: [recipeId], payload: { recipeRestore: recipe } }] };
      },
    },
    'theme.update': {
      action: 'theme.update', label: '修改主题', exposed: 'public', scopes: ['edit'],
      targetIds: () => ['thm_default'],
      apply(content, payload, io) {
        if (payload.themeRestore !== undefined) {
          const restore = asRecord(payload.themeRestore, '主题快照') as unknown as ThemeDoc;
          content.theme = { themeId: restore.themeId ?? DEFAULT_THEME.themeId, dialogue: { ...restore.dialogue }, menu: { ...restore.menu } };
          content.project.updatedAt = io.now;
          return { changedIds: ['thm_default'], summary: '恢复主题', inverse: [] };
        }
        const theme = content.theme ?? DEFAULT_THEME;
        const previous: ThemeDoc = { themeId: theme.themeId, dialogue: { ...theme.dialogue }, menu: { ...theme.menu } };
        const next: ThemeDoc = { themeId: theme.themeId, dialogue: { ...theme.dialogue }, menu: { ...theme.menu } };
        if (payload.textColor !== undefined) next.dialogue.textColor = str(payload, 'textColor', { label: '对白文字颜色', required: true, pattern: /^#[0-9a-f]{6}$/i });
        if (payload.fontSize !== undefined) next.dialogue.fontSize = num(payload, 'fontSize', { label: '对白字号', integer: true, min: 12, max: 48 });
        if (payload.align !== undefined) next.dialogue.align = str(payload, 'align', { label: '对白对齐', required: true, pattern: /^(left|center)$/ }) as 'left' | 'center';
        if (payload.accentColor !== undefined) next.menu.accentColor = str(payload, 'accentColor', { label: '菜单强调色', required: true, pattern: /^#[0-9a-f]{6}$/i });
        next.updatedAt = io.now;
        content.theme = next;
        content.project.updatedAt = io.now;
        return {
          changedIds: ['thm_default'],
          summary: '修改主题（对白排版与强调色）',
          inverse: [{ action: 'theme.update', targetIds: ['thm_default'], payload: { themeRestore: previous } }],
        };
      },
    },
  'variable.create': {
    action: 'variable.create', label: '新建变量', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.variableId === 'string' && payload.variableId ? [payload.variableId] : []),
    apply(content, payload, io) {
      const variableId = idOrNew(payload, 'variableId', ID_PREFIX.variable, '变量 ID');
      if (content.variables.some((variable) => variable.variableId === variableId)) fail(`变量 ${variableId} 已存在`);
      const name = str(payload, 'name', { label: '变量名', required: true, maxLength: 60 });
      const type = str(payload, 'type', { label: '变量类型', required: true, pattern: /^(number|string|boolean)$/ });
      const initial = payload.initial;
      if (typeof initial !== type) fail(`变量初值必须是 ${type} 类型`);
      const scope = payload.scope === 'chapter' ? 'chapter' : 'global';
      const variable: VariableDoc = { variableId, name, type: type as VariableDoc['type'], initial, scope };
      content.variables.push(variable);
      content.project.updatedAt = io.now;
      return {
        changedIds: [variableId],
        summary: `新建变量 ${name}`,
        inverse: [{ action: 'variable.delete', targetIds: [variableId], payload: { variableId } }],
      };
    },
  },

  'variable.update': {
    action: 'variable.update', label: '修改变量', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.variableId === 'string' ? [payload.variableId] : []),
    apply(content, payload, io) {
      const variableId = str(payload, 'variableId', { label: '变量 ID', required: true });
      const variable = content.variables.find((item) => item.variableId === variableId);
      if (!variable) fail(`变量 ${variableId} 不存在`);
      const previous: VariableDoc = { ...variable };
      if (payload.name !== undefined) variable.name = str(payload, 'name', { label: '变量名', required: true, maxLength: 60 });
      if (payload.initial !== undefined) {
        if (typeof payload.initial !== variable.type) fail(`变量初值必须是 ${variable.type} 类型`);
        variable.initial = payload.initial;
      }
      content.project.updatedAt = io.now;
      return {
        changedIds: [variableId],
        summary: `修改变量 ${variable.name}`,
        inverse: [{ action: 'variable.restore', targetIds: [variableId], payload: { snapshot: previous } }],
      };
    },
  },

  'variable.delete': {
    action: 'variable.delete', label: '删除变量', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.variableId === 'string' ? [payload.variableId] : []),
    apply(content, payload, io) {
      const variableId = str(payload, 'variableId', { label: '变量 ID', required: true });
      const variable = content.variables.find((item) => item.variableId === variableId);
      if (!variable) fail(`变量 ${variableId} 不存在`);
      const used = content.scenes.some((scene) => scene.events.some((event) => event.type === 'variable' && event.params.variableId === variableId));
      if (used) fail(`变量 ${variable.name} 仍被事件引用，请先删除引用`);
      content.variables = content.variables.filter((item) => item.variableId !== variableId);
      content.project.updatedAt = io.now;
      return {
        changedIds: [variableId],
        summary: `删除变量 ${variable.name}`,
        inverse: [{ action: 'variable.restore', targetIds: [variableId], payload: { snapshot: variable } }],
      };
    },
  },

  'variable.restore': {
    action: 'variable.restore', label: '恢复变量（撤销用）', exposed: 'internal', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.snapshot === 'object' && payload.snapshot ? [(payload.snapshot as VariableDoc).variableId] : []),
    apply(content, payload, io) {
      const snapshot = asRecord(payload.snapshot, '变量快照') as unknown as VariableDoc;
      const existing = content.variables.findIndex((item) => item.variableId === snapshot.variableId);
      if (existing >= 0) content.variables[existing] = { ...snapshot };
      else content.variables.push({ ...snapshot });
      content.project.updatedAt = io.now;
      return { changedIds: [snapshot.variableId], summary: `恢复变量 ${snapshot.name}`, inverse: [{ action: 'variable.delete', targetIds: [snapshot.variableId], payload: { variableId: snapshot.variableId } }] };
    },
  },

  'character.create': {
    action: 'character.create', label: '新建角色', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.characterId === 'string' && payload.characterId ? [payload.characterId] : []),
    apply(content, payload, io) {
      const characterId = idOrNew(payload, 'characterId', ID_PREFIX.character, '角色 ID');
      if (content.characters.some((character) => character.characterId === characterId)) fail(`角色 ${characterId} 已存在`);
      const name = str(payload, 'name', { label: '角色名', required: true, maxLength: 60 });
      const description = str(payload, 'description', { label: '角色说明', maxLength: 2000 });
      content.characters.push({ characterId, name, description });
      content.project.updatedAt = io.now;
      return { changedIds: [characterId], summary: `新建角色 ${name}`, inverse: [{ action: 'character.delete', targetIds: [characterId], payload: { characterId } }] };
    },
  },

  'character.update': {
    action: 'character.update', label: '修改角色', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.characterId === 'string' ? [payload.characterId] : []),
    apply(content, payload, io) {
      const characterId = str(payload, 'characterId', { label: '角色 ID', required: true });
      const character = content.characters.find((item) => item.characterId === characterId);
      if (!character) fail(`角色 ${characterId} 不存在`);
      const previous = { ...character };
      if (payload.name !== undefined) character.name = str(payload, 'name', { label: '角色名', required: true, maxLength: 60 });
      if (payload.description !== undefined) character.description = str(payload, 'description', { label: '角色说明', maxLength: 2000 });
      content.project.updatedAt = io.now;
      return { changedIds: [characterId], summary: `修改角色 ${character.name}`, inverse: [{ action: 'character.restore', targetIds: [characterId], payload: { snapshot: previous } }] };
    },
  },

  'character.delete': {
    action: 'character.delete', label: '删除角色', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.characterId === 'string' ? [payload.characterId] : []),
    apply(content, payload, io) {
      const characterId = str(payload, 'characterId', { label: '角色 ID', required: true });
      const character = content.characters.find((item) => item.characterId === characterId);
      if (!character) fail(`角色 ${characterId} 不存在`);
      content.characters = content.characters.filter((item) => item.characterId !== characterId);
      content.project.updatedAt = io.now;
      return { changedIds: [characterId], summary: `删除角色 ${character.name}`, inverse: [{ action: 'character.restore', targetIds: [characterId], payload: { snapshot: character } }] };
    },
  },

  'character.restore': {
    action: 'character.restore', label: '恢复角色（撤销用）', exposed: 'internal', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.snapshot === 'object' && payload.snapshot ? [(payload.snapshot as { characterId: string }).characterId] : []),
    apply(content, payload, io) {
      const snapshot = asRecord(payload.snapshot, '角色快照') as unknown as StoryContent['characters'][number];
      const index = content.characters.findIndex((item) => item.characterId === snapshot.characterId);
      if (index >= 0) content.characters[index] = { ...snapshot };
      else content.characters.push({ ...snapshot });
      content.project.updatedAt = io.now;
      return { changedIds: [snapshot.characterId], summary: `恢复角色 ${snapshot.name}`, inverse: [{ action: 'character.delete', targetIds: [snapshot.characterId], payload: { characterId: snapshot.characterId } }] };
    },
  },

  'level.register': {
    action: 'level.register', label: '登记关卡', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.levelId === 'string' && payload.levelId ? [payload.levelId] : []),
    apply(content, payload, io) {
      const levelId = idOrNew(payload, 'levelId', ID_PREFIX.level, '关卡 ID');
      const existing = content.levels.find((level) => level.levelId === levelId);
      const title = str(payload, 'title', { label: '关卡标题', required: true, maxLength: 120 });
      const entry = str(payload, 'entry', { label: '关卡入口', required: true, maxLength: 400 });
      const supportsSnapshot = payload.supportsSnapshot === undefined ? true : payload.supportsSnapshot === true;
      if (existing) {
        const previous: LevelDoc = { ...existing };
        existing.title = title;
        existing.entry = entry;
        existing.supportsSnapshot = supportsSnapshot;
        content.project.updatedAt = io.now;
        return { changedIds: [levelId], summary: `更新关卡《${title}》`, inverse: [{ action: 'level.restore', targetIds: [levelId], payload: { snapshot: previous } }] };
      }
      const level: LevelDoc = { levelId, title, entry, supportsSnapshot };
      content.levels.push(level);
      content.project.updatedAt = io.now;
      return { changedIds: [levelId], summary: `登记关卡《${title}》`, inverse: [{ action: 'level.delete', targetIds: [levelId], payload: { levelId } }] };
    },
  },

  'level.installProgram': {
    action: 'level.installProgram', label: '安装关卡程序', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.levelId === 'string' ? [payload.levelId] : []),
    apply(content, payload, io) {
      const levelId = str(payload, 'levelId', { label: '关卡 ID', required: true });
      const level = content.levels.find((item) => item.levelId === levelId);
      if (!level) fail(`关卡 ${levelId} 未登记，请先调用 level.register`);
      const sourcePath = str(payload, 'sourcePath', { label: '关卡文件', required: true, maxLength: 500 });
      const file = io.readAuthorizedFile(sourcePath);
      if (!/\.html?$/i.test(sourcePath)) fail('关卡程序必须是 .html 文件');
      const entry = str(payload, 'entry', { label: '入口路径', default: `levels/${levelId}.html`, maxLength: 200 });
      if (!entry.startsWith('levels/') || entry.includes('..')) fail('关卡入口必须是 levels/ 下的相对路径');
      const previous: LevelDoc = { ...level };
      level.entry = entry;
      content.project.updatedAt = io.now;
      return {
        changedIds: [levelId],
        summary: `安装关卡《${level.title}》程序（${file.bytes.length} 字节）`,
        inverse: [{ action: 'level.restore', targetIds: [levelId], payload: { snapshot: previous } }],
        binaryFiles: [{ relPath: entry, content: file.bytes }],
      };
    },
  },

  'level.delete': {
    action: 'level.delete', label: '删除关卡', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.levelId === 'string' ? [payload.levelId] : []),
    apply(content, payload, io) {
      const levelId = str(payload, 'levelId', { label: '关卡 ID', required: true });
      const level = content.levels.find((item) => item.levelId === levelId);
      if (!level) fail(`关卡 ${levelId} 不存在`);
      const used = content.scenes.some((scene) => scene.events.some((event) => event.type === 'level' && event.params.levelId === levelId));
      if (used) fail(`关卡《${level.title}》仍被事件引用，请先删除引用`);
      content.levels = content.levels.filter((item) => item.levelId !== levelId);
      content.project.updatedAt = io.now;
      return { changedIds: [levelId], summary: `删除关卡《${level.title}》`, inverse: [{ action: 'level.restore', targetIds: [levelId], payload: { snapshot: level } }] };
    },
  },

  'level.restore': {
    action: 'level.restore', label: '恢复关卡（撤销用）', exposed: 'internal', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.snapshot === 'object' && payload.snapshot ? [(payload.snapshot as LevelDoc).levelId] : []),
    apply(content, payload, io) {
      const snapshot = asRecord(payload.snapshot, '关卡快照') as unknown as LevelDoc;
      const index = content.levels.findIndex((item) => item.levelId === snapshot.levelId);
      if (index >= 0) content.levels[index] = { ...snapshot };
      else content.levels.push({ ...snapshot });
      content.project.updatedAt = io.now;
      return { changedIds: [snapshot.levelId], summary: `恢复关卡《${snapshot.title}》`, inverse: [{ action: 'level.delete', targetIds: [snapshot.levelId], payload: { levelId: snapshot.levelId } }] };
    },
  },

  'asset.registerFromFile': {
    action: 'asset.registerFromFile', label: '导入素材文件', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.assetId === 'string' && payload.assetId ? [payload.assetId] : []),
    apply(content, payload, io) {
      const assetId = idOrNew(payload, 'assetId', ID_PREFIX.asset, '素材 ID');
      if (content.assets.some((asset) => asset.assetId === assetId)) fail(`素材 ${assetId} 已存在`);
      const name = str(payload, 'name', { label: '素材名', required: true, maxLength: 120 });
      const kind = str(payload, 'kind', { label: '素材类型', required: true });
      if (!ASSET_KINDS.includes(kind as AssetDoc['kind'])) fail(`不支持的素材类型 ${kind}，允许：${ASSET_KINDS.join('/')}`);
      const sourcePath = str(payload, 'sourcePath', { label: '来源文件', required: true, maxLength: 500 });
      const file = io.readAuthorizedFile(sourcePath);
      // 只记录文件名，不把本机绝对路径写进正式内容（发布包不能包含本地路径依赖）
      const sourceName = file.absPath.split(/[\\/]/).pop() ?? '';
      return registerAssetBytes(content, io, { assetId, name, kind: kind as AssetDoc['kind'], bytes: file.bytes, sourceKind: 'file', provenance: { sourceName, sourceRedacted: true } });
    },
  },

  'asset.registerGenerated': {
    action: 'asset.registerGenerated', label: '登记生成素材', exposed: 'public', scopes: ['generate'],
    targetIds: (payload) => (typeof payload.assetId === 'string' && payload.assetId ? [payload.assetId] : []),
    apply(content, payload, io) {
      const assetId = idOrNew(payload, 'assetId', ID_PREFIX.asset, '素材 ID');
      if (content.assets.some((asset) => asset.assetId === assetId)) fail(`素材 ${assetId} 已存在`);
      const name = str(payload, 'name', { label: '素材名', required: true, maxLength: 120 });
      const kind = str(payload, 'kind', { label: '素材类型', required: true });
      if (!ASSET_KINDS.includes(kind as AssetDoc['kind'])) fail(`不支持的素材类型 ${kind}`);
      const sourcePath = str(payload, 'sourcePath', { label: '产物文件', required: true, maxLength: 500 });
      const file = io.readAuthorizedFile(sourcePath);
      const provenance = payload.provenance === undefined ? {} : asRecord(payload.provenance, '生成来源');
      return registerAssetBytes(content, io, { assetId, name, kind: kind as AssetDoc['kind'], bytes: file.bytes, sourceKind: 'generated', provenance });
    },
  },

  'asset.bind': {
    action: 'asset.bind', label: '绑定素材到事件字段', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.eventId === 'string' ? [payload.eventId] : []),
    apply(content, payload, io) {
      const sceneId = str(payload, 'sceneId', { label: '场景 ID', required: true });
      const eventId = str(payload, 'eventId', { label: '事件 ID', required: true });
      const field = str(payload, 'field', { label: '字段名', required: true, maxLength: 60 });
      const assetId = str(payload, 'assetId', { label: '素材 ID', required: true });
      const asset = content.assets.find((item) => item.assetId === assetId);
      if (!asset) fail(`素材 ${assetId} 不存在`);
      const { scene, event } = requireEvent(content, sceneId, eventId);
      const spec = EVENT_SPECS[event.type].params.find((param) => param.key === field);
      if (!spec) fail(`事件类型 ${event.type} 没有字段 ${field}`);
      if (spec.type !== 'assetRef') fail(`字段 ${field} 不是素材引用字段`);
      if (spec.assetKinds && !spec.assetKinds.includes(asset.kind)) fail(`字段 ${field} 需要 ${spec.assetKinds.join('/')} 类型素材，${assetId} 是 ${asset.kind}`);
      const previous = event.params[field];
      const params = { ...event.params, [field]: assetId };
      const issues = validateEventParams(event.type, params, eventValidationContext(content));
      if (issues.length) fail('绑定后事件参数不合法', issues);
      event.params = params;
      event.updatedAt = io.now;
      scene.updatedAt = io.now;
      content.project.updatedAt = io.now;
      return {
        changedIds: [sceneId, eventId, assetId],
        summary: `绑定素材 ${asset.name} 到事件 ${eventId}.${field}`,
        inverse: [{ action: 'event.update', targetIds: [eventId], payload: { sceneId, eventId, params: previous === undefined ? { ...event.params, [field]: '' } : { ...event.params, [field]: previous } } }],
      };
    },
  },

  'asset.delete': {
    action: 'asset.delete', label: '解除素材登记', exposed: 'public', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.assetId === 'string' ? [payload.assetId] : []),
    apply(content, payload, io) {
      const assetId = str(payload, 'assetId', { label: '素材 ID', required: true });
      const asset = content.assets.find((item) => item.assetId === assetId);
      if (!asset) fail(`素材 ${assetId} 不存在`);
      const referenced = collectAssetReferences(content, assetId);
      if (referenced.length) fail(`素材 ${asset.name} 仍被 ${referenced.length} 处引用，请先解除引用`, referenced.map((entry) => ({ code: 'ASSET_IN_USE', message: `引用位置：${entry}`, objectId: assetId })));
      content.assets = content.assets.filter((item) => item.assetId !== assetId);
      content.project.updatedAt = io.now;
      return {
        changedIds: [assetId],
        summary: `解除素材登记 ${asset.name}（文件保留在 assets/originals）`,
        inverse: [{ action: 'asset.restore', targetIds: [assetId], payload: { snapshot: asset } }],
      };
    },
  },

  'asset.restore': {
    action: 'asset.restore', label: '恢复素材登记（撤销用）', exposed: 'internal', scopes: ['edit'],
    targetIds: (payload) => (typeof payload.snapshot === 'object' && payload.snapshot ? [(payload.snapshot as AssetDoc).assetId] : []),
    apply(content, payload, io) {
      const snapshot = asRecord(payload.snapshot, '素材快照') as unknown as AssetDoc;
      if (content.assets.some((asset) => asset.assetId === snapshot.assetId)) fail(`素材 ${snapshot.assetId} 已登记`);
      content.assets.push({ ...snapshot });
      content.project.updatedAt = io.now;
      return { changedIds: [snapshot.assetId], summary: `恢复素材登记 ${snapshot.name}`, inverse: [{ action: 'asset.delete', targetIds: [snapshot.assetId], payload: { assetId: snapshot.assetId } }] };
    },
  },
};

export function collectAssetReferences(content: StoryContent, assetId: string): string[] {
  const refs: string[] = [];
  for (const scene of content.scenes) {
    for (const event of scene.events) {
      for (const [key, value] of Object.entries(event.params ?? {})) {
        if (value === assetId) refs.push(`场景 ${scene.sceneId} 事件 ${event.eventId}.${key}`);
        if (Array.isArray(value)) {
          value.forEach((item, index) => {
            if (item && typeof item === 'object' && (item as Record<string, unknown>).assetId === assetId) refs.push(`场景 ${scene.sceneId} 事件 ${event.eventId}.${key}[${index}]`);
          });
        }
      }
    }
  }
  return refs;
}

function registerAssetBytes(
  content: StoryContent,
  io: ActionIO,
  input: { assetId: string; name: string; kind: AssetDoc['kind']; bytes: Buffer; sourceKind: AssetDoc['sourceKind']; provenance: Record<string, unknown> },
): ApplyResult {
  const decoded = decodeAsset(input.bytes, input.kind);
  if (!decoded.ok) fail(`素材文件无法解码：${decoded.message}`, [{ code: 'ASSET_DECODE_FAILED', message: decoded.message }]);
  const ext = decoded.extension;
  const relPath = `assets/originals/${input.assetId}${ext}`;
  const mimeType = decoded.mimeType || guessMimeType(relPath);
  const asset: AssetDoc = {
    assetId: input.assetId,
    name: input.name,
    kind: input.kind,
    path: relPath,
    mimeType,
    sha256: decoded.sha256,
    bytes: input.bytes.length,
    version: '1',
    importedAt: io.now,
    sourceKind: input.sourceKind,
    provenance: { ...input.provenance, width: decoded.width, height: decoded.height, durationSeconds: decoded.durationSeconds },
  };
  content.assets.push(asset);
  content.project.updatedAt = io.now;
  return {
    changedIds: [input.assetId],
    summary: `登记素材 ${input.name}（${input.kind}，${input.bytes.length} 字节）`,
    inverse: [{ action: 'asset.delete', targetIds: [input.assetId], payload: { assetId: input.assetId } }],
    binaryFiles: [{ relPath, content: input.bytes }],
  };
}

export function actionDefinition(action: string): ActionDefinition {
  const definition = ACTIONS[action];
  if (!definition) throw new StudioError('VALIDATION_FAILED', `不支持的操作：${action}`, { issues: [{ code: 'UNKNOWN_ACTION', message: `支持的操作：${Object.keys(ACTIONS).join(', ')}` }] });
  return definition;
}
