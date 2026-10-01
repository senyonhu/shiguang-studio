// StoryContent（内存模型）与工程文件集合之间的转换、比较与装载。
// 工程内容文件的唯一写入者是通过 journal 提交的 store；本模块只负责序列化。
import path from 'node:path';
import { existsSync } from 'node:fs';
import { DEFAULT_THEME } from '../../../src/runtime/content.ts';
import type { AssetDoc, ChapterDoc, CharacterDoc, EventRecord, LevelDoc, ProjectMeta, RecipeDoc, SceneDoc, StoryContent, ThemeDoc, VariableDoc } from '../../../src/runtime/content.ts';
import { PROJECT_FORMAT_VERSION, RUNTIME_KERNEL_VERSION } from '../version.ts';
import type { ProjectPaths } from './paths.ts';
import { ensureDir, listDirNames, readJsonIfExists, readText } from './fsx.ts';
import { makeId, ID_PREFIX } from '../core/ids.ts';

/** relPath(相对工程根, 正斜杠) -> 文件内容 */
export type FileSet = Map<string, string | Buffer>;

export interface LoadedProject {
  content: StoryContent;
  /** 装载时读到的内容文件（不含二进制素材） */
  files: FileSet;
  issues: string[];
}

const withApi = <T extends object>(value: T): T & { apiVersion: string } => ({ apiVersion: '1', ...value });

export function contentFileSet(content: StoryContent): FileSet {
  const files: FileSet = new Map();
  files.set('project.json', `${JSON.stringify(withApi(content.project), null, 2)}\n`);
  for (const chapter of content.chapters) {
    files.set(`story/chapters/${chapter.chapterId}.json`, `${JSON.stringify(withApi(chapter), null, 2)}\n`);
  }
  for (const scene of content.scenes) {
    files.set(`story/scenes/${scene.sceneId}.json`, `${JSON.stringify(withApi(scene), null, 2)}\n`);
  }
  for (const character of content.characters) {
    files.set(`characters/${character.characterId}.json`, `${JSON.stringify(withApi(character), null, 2)}\n`);
  }
  files.set('variables.json', `${JSON.stringify(withApi({ variables: content.variables }), null, 2)}\n`);
  files.set('assets/manifest.json', `${JSON.stringify(withApi({ assets: content.assets }), null, 2)}\n`);
  for (const level of content.levels) {
    files.set(`levels/${level.levelId}.json`, `${JSON.stringify(withApi(level), null, 2)}\n`);
  }
  files.set('themes/theme.json', `${JSON.stringify(withApi(content.theme ?? DEFAULT_THEME), null, 2)}\n`);
  for (const recipe of content.recipes ?? []) files.set(`generation/recipes/${recipe.recipeId}.json`, `${JSON.stringify(withApi(recipe), null, 2)}\n`);
  return files;
}

export function loadProject(paths: ProjectPaths): LoadedProject {
  const issues: string[] = [];
  const files: FileSet = new Map();
  const read = (rel: string): unknown => {
    const abs = path.join(paths.root, rel);
    if (!existsSync(abs)) return null;
    const text = readText(abs);
    files.set(rel, text);
    try { return JSON.parse(text); } catch { issues.push(`${rel} 不是有效 JSON`); return null; }
  };

  const projectRaw = read('project.json') as (ProjectMeta & { apiVersion?: string }) | null;
  if (!projectRaw) {
    throw new Error(`工程 ${paths.projectId} 缺少 project.json`);
  }
  const chapters: ChapterDoc[] = [];
  for (const name of listDirNames(paths.chaptersDir)) {
    if (!name.endsWith('.json')) continue;
    const value = read(`story/chapters/${name}`) as ChapterDoc | null;
    if (value) chapters.push({ chapterId: value.chapterId, title: value.title, order: value.order, sceneIds: value.sceneIds ?? [], updatedAt: value.updatedAt });
  }
  const scenes: SceneDoc[] = [];
  for (const name of listDirNames(paths.scenesDir)) {
    if (!name.endsWith('.json')) continue;
    const value = read(`story/scenes/${name}`) as SceneDoc | null;
    if (value) scenes.push({ sceneId: value.sceneId, chapterId: value.chapterId, title: value.title, events: (value.events ?? []) as EventRecord[], updatedAt: value.updatedAt });
  }
  const characters: CharacterDoc[] = [];
  for (const name of listDirNames(paths.charactersDir)) {
    if (!name.endsWith('.json')) continue;
    const value = read(`characters/${name}`) as CharacterDoc | null;
    if (value) characters.push(value);
  }
  const variablesRaw = read('variables.json') as { variables?: VariableDoc[] } | null;
  const assetsRaw = read('assets/manifest.json') as { assets?: AssetDoc[] } | null;
  const levels: LevelDoc[] = [];
  for (const name of listDirNames(paths.levelsDir)) {
    if (!name.endsWith('.json')) continue;
    const value = read(`levels/${name}`) as LevelDoc | null;
    if (value) levels.push(value);
  }
  const themeRaw = read('themes/theme.json') as ThemeDoc | null;
  const recipes: RecipeDoc[] = [];
  if (existsSync(paths.recipesDir)) {
    for (const name of listDirNames(paths.recipesDir)) {
      if (!name.endsWith('.json')) continue;
      const value = read(`generation/recipes/${name}`) as RecipeDoc | null;
      if (value && typeof value.recipeId === 'string') recipes.push(value);
    }
  }

  const content: StoryContent = {
    project: {
      projectId: projectRaw.projectId ?? paths.projectId,
      title: projectRaw.title ?? paths.projectId,
      formatVersion: projectRaw.formatVersion ?? PROJECT_FORMAT_VERSION,
      entrySceneId: projectRaw.entrySceneId ?? '',
      chapterIds: projectRaw.chapterIds ?? chapters.map((chapter) => chapter.chapterId),
      createdAt: projectRaw.createdAt ?? new Date().toISOString(),
      updatedAt: projectRaw.updatedAt ?? new Date().toISOString(),
      runtimeCompat: projectRaw.runtimeCompat ?? `studio-runtime/${RUNTIME_KERNEL_VERSION}`,
    },
    chapters: chapters.sort((a, b) => a.order - b.order),
    scenes,
    variables: variablesRaw?.variables ?? [],
    assets: assetsRaw?.assets ?? [],
    characters,
    levels,
    recipes: recipes.sort((a, b) => (a.recipeId < b.recipeId ? -1 : 1)),
    theme: {
      themeId: themeRaw?.themeId ?? DEFAULT_THEME.themeId,
      dialogue: {
        textColor: themeRaw?.dialogue?.textColor ?? DEFAULT_THEME.dialogue.textColor,
        fontSize: themeRaw?.dialogue?.fontSize ?? DEFAULT_THEME.dialogue.fontSize,
        align: themeRaw?.dialogue?.align === 'center' ? 'center' : DEFAULT_THEME.dialogue.align,
      },
      menu: { accentColor: themeRaw?.menu?.accentColor ?? DEFAULT_THEME.menu.accentColor },
      ...(themeRaw?.updatedAt ? { updatedAt: themeRaw.updatedAt } : {}),
    },
  };
  return { content, files, issues };
}

export function emptyContent(projectId: string, title: string, now: string): StoryContent {
  return {
    project: {
      projectId,
      title,
      formatVersion: PROJECT_FORMAT_VERSION,
      entrySceneId: '',
      chapterIds: [],
      createdAt: now,
      updatedAt: now,
      runtimeCompat: `studio-runtime/${RUNTIME_KERNEL_VERSION}`,
    },
    chapters: [],
    scenes: [],
    variables: [],
    assets: [],
    characters: [],
    levels: [],
  };
}

export interface FileChange { relPath: string; content: string | Buffer | null }

/** 比较新旧文件集合，返回需要写入/删除的文件（未变化的文件不写盘）。 */
export function diffFileSets(previous: FileSet, next: FileSet): FileChange[] {
  const changes: FileChange[] = [];
  for (const [relPath, content] of next) {
    const old = previous.get(relPath);
    if (typeof content === 'string' && typeof old === 'string' && old === content) continue;
    if (Buffer.isBuffer(content) && Buffer.isBuffer(old) && old.equals(content)) continue;
    changes.push({ relPath, content });
  }
  for (const relPath of previous.keys()) {
    if (!next.has(relPath)) changes.push({ relPath, content: null });
  }
  return changes.sort((a, b) => (a.relPath < b.relPath ? -1 : 1));
}

export function planFromContent(previous: FileSet, content: StoryContent): FileChange[] {
  return diffFileSets(previous, contentFileSet(content));
}

/**
 * 只序列化与变更对象相关的文件（增量保存）。
 * 映射依据：动作返回的 changedIds 覆盖了它修改的全部对象；工程文件始终重写（章节列表/入口场景可能变化）。
 * `STUDIO_VERIFY_PLAN` 或 verifyPlan 选项打开时，会与全量计算结果比对，防止映射遗漏导致漏写。
 */
export function planFromChangedIds(content: StoryContent, previous: FileSet, changedIds: string[]): FileChange[] {
  const rels = new Set<string>(['project.json']);
  for (const objectId of changedIds) {
    const underscore = objectId.indexOf('_');
    const prefix = underscore > 0 ? objectId.slice(0, underscore) : '';
    if (prefix === 'ch') rels.add(`story/chapters/${objectId}.json`);
    else if (prefix === 'sc') rels.add(`story/scenes/${objectId}.json`);
    else if (prefix === 'chr') rels.add(`characters/${objectId}.json`);
    else if (prefix === 'lv') rels.add(`levels/${objectId}.json`);
    else if (prefix === 'as') rels.add('assets/manifest.json');
    else if (prefix === 'var') rels.add('variables.json');
    else if (prefix === 'thm') rels.add('themes/theme.json');
    else if (prefix === 'rcp') rels.add(`generation/recipes/${objectId}.json`);
  }
  // 注意：这里不能复用 diffFileSets——它按“完整文件集合”比较，传入子集会把未列出的文件误判为删除。
  const changes: FileChange[] = [];
  for (const rel of [...rels].sort()) {
    const serialized = serializeContentFile(content, rel);
    const before = previous.get(rel);
    if (serialized === null) {
      if (before !== undefined) changes.push({ relPath: rel, content: null });
      continue;
    }
    if (typeof before === 'string' && before === serialized) continue;
    changes.push({ relPath: rel, content: serialized });
  }
  return changes;
}

/** 按相对路径序列化单个内容文件；不存在（对象已删除）时返回 null。 */
export function serializeContentFile(content: StoryContent, relPath: string): string | null {
  const json = (value: unknown): string => `${JSON.stringify(withApi(value as object), null, 2)}\n`;
  if (relPath === 'project.json') return json(content.project);
  if (relPath === 'variables.json') return json({ variables: content.variables });
  if (relPath === 'assets/manifest.json') return json({ assets: content.assets });
  if (relPath === 'themes/theme.json') return json(content.theme ?? DEFAULT_THEME);
  const chapterMatch = /^story\/chapters\/(.+)\.json$/.exec(relPath);
  if (chapterMatch) {
    const chapter = content.chapters.find((item) => item.chapterId === chapterMatch[1]);
    return chapter ? json(chapter) : null;
  }
  const sceneMatch = /^story\/scenes\/(.+)\.json$/.exec(relPath);
  if (sceneMatch) {
    const scene = content.scenes.find((item) => item.sceneId === sceneMatch[1]);
    return scene ? json(scene) : null;
  }
  const characterMatch = /^characters\/(.+)\.json$/.exec(relPath);
  if (characterMatch) {
    const character = content.characters.find((item) => item.characterId === characterMatch[1]);
    return character ? json(character) : null;
  }
  const recipeMatch = /^generation\/recipes\/(.+)\.json$/.exec(relPath);
  if (recipeMatch) {
    const recipe = (content.recipes ?? []).find((item) => item.recipeId === recipeMatch[1]);
    return recipe ? json(recipe) : null;
  }
  const levelMatch = /^levels\/(.+)\.json$/.exec(relPath);
  if (levelMatch) {
    const level = content.levels.find((item) => item.levelId === levelMatch[1]);
    return level ? json(level) : null;
  }
  return null;
}

export function cloneContent(content: StoryContent): StoryContent {
  // structuredClone 比 JSON 往返快数倍，且能保留同样的纯数据语义（内容模型只有 JSON 可表达的值）。
  return structuredClone(content);
}

/** 对象 ID 形如 prj_xxx / ch_xxx / sc_xxx / ev_xxx / chr_xxx / lv_xxx / as_xxx / var_xxx */
const OBJECT_ID_PATTERN = /^(prj|ch|sc|ev|chr|lv|as|var)_[a-z0-9]+$/i;

/** 递归收集载荷中出现的所有对象 ID（含嵌套选项里的 targetSceneId/targetEventId 等）。 */
export function collectObjectIds(value: unknown, into = new Set<string>()): Set<string> {
  if (typeof value === 'string') {
    if (OBJECT_ID_PATTERN.test(value)) into.add(value);
    return into;
  }
  if (Array.isArray(value)) {
    for (const item of value) collectObjectIds(item, into);
    return into;
  }
  if (value && typeof value === 'object') {
    for (const item of Object.values(value as Record<string, unknown>)) collectObjectIds(item, into);
  }
  return into;
}

/**
 * 按需工作副本：只克隆将要被改动的对象，其余对象与缓存共享（只读）。
 *
 * 为什么需要它：10 万事件工程下 `cloneContent` 整份克隆耗时 p50 95.8ms（见 KI-026），
 * 而一次普通编辑只会碰到一两个对象。
 *
 * 正确性保证：
 *  1. 候选集合来自「动作声明的 targetIds」+「载荷中出现的全部对象 ID」+「父对象」（场景所属章节）；
 *  2. 场景被克隆时连 `events` 数组与其中每个事件对象一起克隆，章节克隆时连 `sceneIds` 数组一起克隆
 *     —— 这两处是动作会就地修改的可变结构；
 *  3. `verifyPlan` 模式下会用事务前的原始内容做一次全量比对：任何**计划之外**的文件内容变化都会立刻报错，
 *     从而把“漏克隆导致缓存被就地改写”变成显式失败（tests/studio/unit/incremental-save.test.ts 全程开启）。
 */
export function workingCopyOf(content: StoryContent, touchIds: Iterable<string>): StoryContent {
  const copy: StoryContent = {
    ...content,
    project: { ...content.project },
    chapters: content.chapters.slice(),
    scenes: content.scenes.slice(),
    variables: content.variables.slice(),
    assets: content.assets.slice(),
    characters: content.characters.slice(),
    levels: content.levels.slice(),
  };
  const touchedScenes = new Set<string>();
  for (const objectId of touchIds) {
    const underscore = objectId.indexOf('_');
    const prefix = underscore > 0 ? objectId.slice(0, underscore).toLowerCase() : '';
    if (prefix === 'sc') {
      const index = copy.scenes.findIndex((scene) => scene.sceneId === objectId);
      if (index >= 0) {
        touchedScenes.add(objectId);
        copy.scenes[index] = { ...copy.scenes[index], events: copy.scenes[index].events.map((event) => ({ ...event })) };
      }
    } else if (prefix === 'ch') {
      const index = copy.chapters.findIndex((chapter) => chapter.chapterId === objectId);
      if (index >= 0) copy.chapters[index] = { ...copy.chapters[index], sceneIds: [...copy.chapters[index].sceneIds] };
    } else if (prefix === 'chr') {
      const index = copy.characters.findIndex((character) => character.characterId === objectId);
      if (index >= 0) copy.characters[index] = { ...copy.characters[index] };
    } else if (prefix === 'lv') {
      const index = copy.levels.findIndex((level) => level.levelId === objectId);
      if (index >= 0) copy.levels[index] = { ...copy.levels[index] };
    } else if (prefix === 'var') {
      const index = copy.variables.findIndex((variable) => variable.variableId === objectId);
      if (index >= 0) copy.variables[index] = { ...copy.variables[index] };
    } else if (prefix === 'as') {
      const index = copy.assets.findIndex((asset) => asset.assetId === objectId);
      if (index >= 0) copy.assets[index] = { ...copy.assets[index] };
    } else if (prefix === 'ev') {
      // 事件：所属场景必须一起克隆（events 数组与事件对象会被就地修改）
      const sceneIndex = copy.scenes.findIndex((scene) => scene.events.some((event) => event.eventId === objectId));
      if (sceneIndex >= 0 && !touchedScenes.has(copy.scenes[sceneIndex].sceneId)) {
        const scene = copy.scenes[sceneIndex];
        touchedScenes.add(scene.sceneId);
        copy.scenes[sceneIndex] = { ...scene, events: scene.events.map((event) => ({ ...event })) };
      }
    }
  }
  // 父章节：chapter.sceneIds 会被场景增删改动
  for (const sceneId of touchedScenes) {
    for (const [index, chapter] of copy.chapters.entries()) {
      if (chapter.sceneIds.includes(sceneId) && chapter.sceneIds === content.chapters.find((item) => item.chapterId === chapter.chapterId)?.sceneIds) {
        copy.chapters[index] = { ...chapter, sceneIds: [...chapter.sceneIds] };
      }
    }
  }
  return copy;
}

export function newProjectId(): string {
  return makeId(ID_PREFIX.project);
}

export function ensureProjectDirs(paths: ProjectPaths): void {
  for (const dir of [paths.root, paths.chaptersDir, paths.scenesDir, paths.charactersDir, paths.assetsDir, paths.originalsDir, paths.derivedDir, paths.levelsDir, paths.themesDir, paths.recipesDir, paths.testsDir, paths.generationDir, paths.studioDir, paths.historyDir, paths.journalDir, paths.jobsDir, paths.snapshotsDir, paths.exportsDir]) {
    ensureDir(dir);
  }
}

export function readStudioState<T>(paths: ProjectPaths): T | null {
  return readJsonIfExists<T>(paths.stateJson);
}
