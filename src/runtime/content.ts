// 正式工程的内容模型：后台持久化、编辑器、展示器和运行内核共用同一份类型。
// 这里只描述内容；事务、历史、任务等运行数据由后台的 .studio 目录单独管理。
import type { AssetKind, EventType } from './events.ts';
import { eventSummary, isEventType, validateEventParams, validateEventTarget } from './events.ts';
import type { ParamIssue } from './events.ts';
import { PROJECT_FORMAT_VERSION } from './version.ts';

export interface EventRecord {
  eventId: string;
  type: EventType;
  params: Record<string, unknown>;
  createdAt?: string;
  updatedAt?: string;
}

export interface SceneDoc {
  sceneId: string;
  chapterId: string;
  title: string;
  events: EventRecord[];
  updatedAt?: string;
}

export interface ChapterDoc {
  chapterId: string;
  title: string;
  order: number;
  sceneIds: string[];
  updatedAt?: string;
}

export type VariableType = 'number' | 'string' | 'boolean';

export interface VariableDoc {
  variableId: string;
  name: string;
  type: VariableType;
  initial: unknown;
  scope: 'global' | 'chapter';
}

export interface AssetDoc {
  assetId: string;
  name: string;
  kind: AssetKind;
  /** 工程内相对路径，形如 assets/originals/as_xxx.png */
  path: string;
  mimeType: string;
  sha256: string;
  bytes: number;
  version: string;
  importedAt: string;
  sourceKind: 'file' | 'generated' | 'fixture';
  /** 生成类素材的来源信息（服务、模型、任务、提示词散列），人工导入时为空 */
  provenance?: Record<string, unknown>;
  /** 播放副本相对路径（可再生成） */
  derivedPath?: string;
}

export interface CharacterDoc {
  characterId: string;
  name: string;
  description?: string;
  expressions?: { label: string; assetId: string }[];
}

export interface LevelDoc {
  levelId: string;
  title: string;
  entry: string;
  supportsSnapshot: boolean;
}

export interface ProjectMeta {
  projectId: string;
  title: string;
  formatVersion: number;
  entrySceneId: string;
  chapterIds: string[];
  createdAt: string;
  updatedAt: string;
  runtimeCompat: string;
}

export interface StoryContent {
  project: ProjectMeta;
  chapters: ChapterDoc[];
  scenes: SceneDoc[];
  variables: VariableDoc[];
  assets: AssetDoc[];
  characters: CharacterDoc[];
  levels: LevelDoc[];
  /** 主题：磁盘上始终存在（themes/theme.json），加载时补齐默认值；校验要求它必须存在。 */
  theme?: ThemeDoc;
  /** 生成模板（recipes/<id>.json）：给图片/视频生成预设提示词与参数。 */
  recipes?: RecipeDoc[];
}

/** 生成模板：预设的提示词与参数，供人工界面与外部 AI 复用同一套生成配置。 */
export interface RecipeDoc {
  recipeId: string;
  name: string;
  taskType: 'image.text' | 'image.edit' | 'video.text' | 'video.image';
  prompt: string;
  negativePrompt?: string;
  params?: Record<string, unknown>;
  updatedAt?: string;
}

export const RECIPE_TASK_TYPES: RecipeDoc['taskType'][] = ['image.text', 'image.edit', 'video.text', 'video.image'];

/** 主题（themes/theme.json）：目前只包含对白排版与菜单强调色，编辑器面板与展示器共用同一份数据。 */
export interface ThemeDoc {
  themeId: string;
  dialogue: {
    /** 对白文字颜色（#rrggbb） */
    textColor: string;
    /** 对白字号（像素，12–48） */
    fontSize: number;
    /** 对白对齐方式 */
    align: 'left' | 'center';
  };
  menu: {
    /** 选项/按钮强调色（#rrggbb） */
    accentColor: string;
  };
  updatedAt?: string;
}

export const DEFAULT_THEME: ThemeDoc = {
  themeId: 'default',
  dialogue: { textColor: '#e8eef7', fontSize: 18, align: 'left' },
  menu: { accentColor: '#7fb2ff' },
};

export interface StoryIssue extends ParamIssue { path: string; objectId?: string }

export const ASSET_KINDS: AssetKind[] = ['background', 'character', 'cg', 'audio', 'video'];

export function assetKindMap(content: StoryContent): Record<string, AssetKind> {
  const map: Record<string, AssetKind> = {};
  for (const asset of content.assets) map[asset.assetId] = asset.kind;
  return map;
}

export function sceneById(content: StoryContent, sceneId: string): SceneDoc | undefined {
  return content.scenes.find((scene) => scene.sceneId === sceneId);
}

export function eventIdsByScene(content: StoryContent): Record<string, string[]> {
  const map: Record<string, string[]> = {};
  for (const scene of content.scenes) map[scene.sceneId] = scene.events.map((event) => event.eventId);
  return map;
}

export function initialVariables(content: StoryContent): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const variable of content.variables) values[variable.variableId] = variable.initial;
  return values;
}

export interface ValidateOptions {
  /**
   * 跳过这些场景的事件参数校验（只做结构与全局一致性检查）。
   * 用于增量校验：未变更的场景在上次提交时已校验过，重跑整份工程的参数校验在大工程下代价过高。
   * 只有调用方确认这些场景“自上次成功校验后未发生变化”时才可以使用。
   */
  skipEventParamsForScenes?: Set<string> | string[];
}

/** 结构化校验：事务提交前与 studio_validate_project 共用。 */
export function validateStory(content: StoryContent, options: ValidateOptions = {}): StoryIssue[] {
  const issues: StoryIssue[] = [];
  const skipSet = options.skipEventParamsForScenes instanceof Set
    ? options.skipEventParamsForScenes
    : new Set(options.skipEventParamsForScenes ?? []);
  const push = (path: string, issue: ParamIssue, objectId?: string) => issues.push({ path, code: issue.code, message: issue.message, field: issue.field, objectId });

  const meta = content.project;
  if (!meta || typeof meta !== 'object') return [{ path: 'project', code: 'INVALID', message: '缺少工程信息' }];
  if (typeof meta.title !== 'string' || !meta.title.trim() || meta.title.length > 120) push('project.title', { code: 'INVALID', message: '工程标题必须是 1–120 字' }, meta.projectId);
  if (meta.formatVersion !== PROJECT_FORMAT_VERSION) push('project.formatVersion', { code: 'UNSUPPORTED_FORMAT', message: `仅支持格式版本 ${PROJECT_FORMAT_VERSION}` });

  const chapterIds = new Set<string>();
  for (const [index, chapter] of content.chapters.entries()) {
    const path = `chapters[${index}]`;
    if (chapterIds.has(chapter.chapterId)) push(`${path}.chapterId`, { code: 'DUPLICATE_ID', message: `章节 ID 重复：${chapter.chapterId}` }, chapter.chapterId);
    chapterIds.add(chapter.chapterId);
    if (typeof chapter.title !== 'string' || !chapter.title.trim() || chapter.title.length > 120) push(`${path}.title`, { code: 'INVALID', message: '章节标题必须是 1–120 字' }, chapter.chapterId);
    if (!Number.isInteger(chapter.order)) push(`${path}.order`, { code: 'INVALID', message: '排序必须是整数' }, chapter.chapterId);
  }
  if (!meta.chapterIds.every((id) => chapterIds.has(id))) push('project.chapterIds', { code: 'MISSING_REF', message: '工程章节列表引用了不存在的章节' }, meta.projectId);
  if (meta.chapterIds.length !== content.chapters.length) push('project.chapterIds', { code: 'INCONSISTENT', message: '工程章节列表与章节文件数量不一致' }, meta.projectId);

  const sceneIds = new Set<string>();
  const sceneToChapter = new Map<string, string>();
  const eventIds = new Set<string>();
  for (const [index, scene] of content.scenes.entries()) {
    const path = `scenes[${index}]`;
    if (sceneIds.has(scene.sceneId)) push(`${path}.sceneId`, { code: 'DUPLICATE_ID', message: `场景 ID 重复：${scene.sceneId}` }, scene.sceneId);
    sceneIds.add(scene.sceneId);
    sceneToChapter.set(scene.sceneId, scene.chapterId);
    if (!chapterIds.has(scene.chapterId)) push(`${path}.chapterId`, { code: 'MISSING_REF', message: `场景引用的章节 ${scene.chapterId} 不存在` }, scene.sceneId);
    if (typeof scene.title !== 'string' || !scene.title.trim() || scene.title.length > 120) push(`${path}.title`, { code: 'INVALID', message: '场景标题必须是 1–120 字' }, scene.sceneId);
    for (const [eventIndex, event] of scene.events.entries()) {
      const eventPath = `${path}.events[${eventIndex}]`;
      if (eventIds.has(event.eventId)) push(`${eventPath}.eventId`, { code: 'DUPLICATE_ID', message: `事件 ID 重复：${event.eventId}` }, event.eventId);
      eventIds.add(event.eventId);
      if (!isEventType(event.type)) push(`${eventPath}.type`, { code: 'UNKNOWN_EVENT_TYPE', message: `不支持的事件类型 ${String(event.type)}` }, event.eventId);
    }
  }
  for (const chapter of content.chapters) {
    for (const sceneId of chapter.sceneIds) {
      if (!sceneIds.has(sceneId)) push(`chapters.${chapter.chapterId}.sceneIds`, { code: 'MISSING_REF', message: `章节引用的场景 ${sceneId} 不存在` }, chapter.chapterId);
      else if (sceneToChapter.get(sceneId) !== chapter.chapterId) push(`chapters.${chapter.chapterId}.sceneIds`, { code: 'INCONSISTENT', message: `场景 ${sceneId} 所属章节与章节列表不一致` }, chapter.chapterId);
    }
    const owned = content.scenes.filter((scene) => scene.chapterId === chapter.chapterId).map((scene) => scene.sceneId);
    if (owned.length !== chapter.sceneIds.length) push(`chapters.${chapter.chapterId}.sceneIds`, { code: 'INCONSISTENT', message: `章节 ${chapter.chapterId} 的场景列表不完整` }, chapter.chapterId);
  }
  if (!sceneIds.has(meta.entrySceneId)) push('project.entrySceneId', { code: 'MISSING_REF', message: `入口场景 ${meta.entrySceneId} 不存在` }, meta.projectId);

  const assetIds = new Set<string>();
  for (const [index, asset] of content.assets.entries()) {
    const path = `assets[${index}]`;
    if (assetIds.has(asset.assetId)) push(`${path}.assetId`, { code: 'DUPLICATE_ID', message: `素材 ID 重复：${asset.assetId}` }, asset.assetId);
    assetIds.add(asset.assetId);
    if (!ASSET_KINDS.includes(asset.kind)) push(`${path}.kind`, { code: 'INVALID', message: `不支持的素材类型 ${String(asset.kind)}` }, asset.assetId);
    if (typeof asset.path !== 'string' || !asset.path.startsWith('assets/')) push(`${path}.path`, { code: 'INVALID', message: '素材路径必须是 assets/ 下的相对路径' }, asset.assetId);
    if (typeof asset.sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(asset.sha256)) push(`${path}.sha256`, { code: 'INVALID', message: '素材缺少内容散列' }, asset.assetId);
  }

  const variableIds = new Set<string>();
  for (const [index, variable] of content.variables.entries()) {
    const path = `variables[${index}]`;
    if (variableIds.has(variable.variableId)) push(`${path}.variableId`, { code: 'DUPLICATE_ID', message: `变量 ID 重复：${variable.variableId}` }, variable.variableId);
    variableIds.add(variable.variableId);
    if (!['number', 'string', 'boolean'].includes(variable.type)) push(`${path}.type`, { code: 'INVALID', message: '变量类型必须是 number/string/boolean' }, variable.variableId);
    else if (typeof variable.initial !== variable.type) push(`${path}.initial`, { code: 'INVALID', message: `变量初值必须是 ${variable.type}` }, variable.variableId);
  }

  const characterIds = new Set<string>();
  for (const [index, character] of content.characters.entries()) {
    if (characterIds.has(character.characterId)) push(`characters[${index}]`, { code: 'DUPLICATE_ID', message: `角色 ID 重复：${character.characterId}` }, character.characterId);
    characterIds.add(character.characterId);
  }
  const levelIds = new Set<string>();
  for (const [index, level] of content.levels.entries()) {
    if (levelIds.has(level.levelId)) push(`levels[${index}]`, { code: 'DUPLICATE_ID', message: `关卡 ID 重复：${level.levelId}` }, level.levelId);
    levelIds.add(level.levelId);
  }

  // 主题：颜色与字号必须合法，否则编辑器面板会把无法识别的值写进正式内容。
  const theme = content.theme;
  if (!theme || typeof theme !== 'object') {
    push('themes/theme.json', { code: 'REQUIRED', message: '缺少主题配置' });
  } else {
    const isColor = (value: unknown): boolean => typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value);
    if (!isColor(theme.dialogue?.textColor)) push('themes/theme.json.dialogue.textColor', { code: 'INVALID', message: '对白文字颜色必须是 #rrggbb' });
    if (typeof theme.dialogue?.fontSize !== 'number' || !Number.isFinite(theme.dialogue.fontSize) || theme.dialogue.fontSize < 12 || theme.dialogue.fontSize > 48) {
      push('themes/theme.json.dialogue.fontSize', { code: 'INVALID', message: '对白字号必须是 12–48 之间的数字' });
    }
    if (theme.dialogue?.align !== 'left' && theme.dialogue?.align !== 'center') push('themes/theme.json.dialogue.align', { code: 'INVALID', message: '对白对齐只能是 left 或 center' });
    if (!isColor(theme.menu?.accentColor)) push('themes/theme.json.menu.accentColor', { code: 'INVALID', message: '菜单强调色必须是 #rrggbb' });
  }

  // 生成模板：任务类型与提示词必须合法（模板会被直接用于生成请求）。
  const recipeIds = new Set<string>();
  for (const [index, recipe] of (content.recipes ?? []).entries()) {
    if (recipeIds.has(recipe.recipeId)) push(`recipes[${index}]`, { code: 'DUPLICATE_ID', message: `生成模板 ID 重复：${recipe.recipeId}` }, recipe.recipeId);
    recipeIds.add(recipe.recipeId);
    if (typeof recipe.name !== 'string' || !recipe.name.trim() || recipe.name.length > 60) push(`recipes[${index}].name`, { code: 'INVALID', message: '模板名必须是 1–60 字' }, recipe.recipeId);
    if (!RECIPE_TASK_TYPES.includes(recipe.taskType)) push(`recipes[${index}].taskType`, { code: 'INVALID', message: `任务类型必须是 ${RECIPE_TASK_TYPES.join(' / ')}` }, recipe.recipeId);
    if (typeof recipe.prompt !== 'string' || !recipe.prompt.trim() || recipe.prompt.length > 2000) push(`recipes[${index}].prompt`, { code: 'INVALID', message: '提示词必须是 1–2000 字' }, recipe.recipeId);
    if (recipe.negativePrompt !== undefined && (typeof recipe.negativePrompt !== 'string' || recipe.negativePrompt.length > 1000)) push(`recipes[${index}].negativePrompt`, { code: 'INVALID', message: '负向提示词最多 1000 字' }, recipe.recipeId);
  }

  const ctx = {
    assets: assetKindMap(content),
    sceneIds: [...sceneIds],
    eventIds: eventIdsByScene(content),
    variableIds: [...variableIds],
    levelIds: [...levelIds],
    characterIds: [...characterIds],
  };
  for (const scene of content.scenes) {
    const fullValidation = !skipSet.has(scene.sceneId);
    for (const [index, event] of scene.events.entries()) {
      if (!isEventType(event.type)) continue;
      if (fullValidation) {
        for (const issue of validateEventParams(event.type, event.params, ctx)) {
          push(`scenes.${scene.sceneId}.events[${index}].${issue.field ?? ''}`.replace(/\.$/, ''), issue, event.eventId);
        }
      }
      // 跨对象引用检查对所有场景执行：未变更场景的参数在上次校验后没变，
      // 但它们引用的场景/事件可能被本次事务删除，仍然必须检查。
      if (!fullValidation && event.type === 'jump') {
        for (const issue of validateEventTarget(event.params, ctx)) {
          push(`scenes.${scene.sceneId}.events[${index}].${issue.field}`, issue, event.eventId);
        }
      }
      if (!fullValidation && event.type === 'choice') {
        const options = Array.isArray(event.params.options) ? event.params.options as Record<string, unknown>[] : [];
        for (const [optionIndex, option] of options.entries()) {
          if (!option || typeof option !== 'object' || Array.isArray(option)) continue;
          for (const issue of validateEventTarget(option, ctx, `options[${optionIndex}]`)) {
            push(`scenes.${scene.sceneId}.events[${index}].${issue.field}`, issue, event.eventId);
          }
        }
      }
    }
  }
  return issues;
}

/** 供编辑器和工具显示的摘要列表。 */
export function sceneOutline(content: StoryContent) {
  return content.chapters
    .slice()
    .sort((a, b) => a.order - b.order)
    .map((chapter) => ({
      chapterId: chapter.chapterId,
      title: chapter.title,
      scenes: chapter.sceneIds.map((sceneId) => {
        const scene = content.scenes.find((item) => item.sceneId === sceneId);
        return {
          sceneId,
          title: scene?.title ?? '(缺失场景)',
          eventCount: scene?.events.length ?? 0,
        };
      }),
    }));
}

export function describeEvents(content: StoryContent, sceneId: string) {
  const scene = sceneById(content, sceneId);
  if (!scene) return [];
  return scene.events.map((event, index) => ({
    index,
    eventId: event.eventId,
    type: event.type,
    summary: eventSummary(event.type, event.params),
    params: event.params,
  }));
}
