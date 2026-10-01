<script setup lang="ts">
// 人工可视化编辑器（后台工程视图）：读取与修改都通过后台 API，与 AI 工具共用同一编辑服务。
import { computed, onMounted, onUnmounted, ref } from 'vue';
import EventParamEditor from './EventParamEditor.vue';
import { eventFormIssues } from './form-helpers.ts';
import { LiveApi, LiveApiError, defaultApiBase } from './api.ts';
import type { Outline, ProjectSummary, SceneDetail, SceneEvent, ProjectExport } from './api.ts';
import { EVENT_SPECS, EVENT_TYPES, defaultEventParams } from '../../runtime/events.ts';
import type { EventType } from '../../runtime/events.ts';
import type { AssetDoc } from '../../runtime/content.ts';

const api = new LiveApi(defaultApiBase());
const apiBase = ref(api.baseUrl);

const connected = ref(false);
const health = ref<{ serviceVersion: string; pid: number } | null>(null);
const fatal = ref('');
const message = ref('');
const pendingRequests = ref(0);
const initializing = ref(false);
const projectLoading = ref(false);
const busy = computed(() => pendingRequests.value > 0 || initializing.value || projectLoading.value);
let outlineGeneration = 0;
let sceneGeneration = 0;
let validationGeneration = 0;
const projects = ref<ProjectSummary[]>([]);
const projectId = ref('');
const outline = ref<Outline | null>(null);
const revision = ref('');
const assets = ref<AssetDoc[]>([]);
const sceneId = ref('');
const scene = ref<SceneDetail | null>(null);
const selectedEventId = ref('');
const draft = ref<Record<string, unknown> | null>(null);
const draftTarget = ref<{ mode: 'create' | 'edit'; projectId: string; sceneId: string; eventId?: string; type: EventType; baseRevision: string; index?: number } | null>(null);
const draftNotice = ref('');
const changeLog = ref<{ at: string; actor: string; summary: string; changedIds: string[] }[]>([]);
const issues = ref<{ path: string; message: string }[]>([]);
const lastUndo = ref<{ undoId: string; summary: string } | null>(null);
const aiSessions = ref<{ sessionId: string; actorId: string; label: string; paused: boolean }[]>([]);
const activePanel = ref<'assets' | 'variables' | 'characters' | 'levels' | 'theme' | 'recipes' | 'history'>('assets');
const insertType = ref<EventType>('dialogue');
const exportedProject = ref<ProjectExport | null>(null);
const validationState = ref<'idle' | 'checking' | 'passed' | 'failed' | 'unavailable'>('idle');
const playbackResult = ref<{ url: string; revision: string; expiresAt: string } | null>(null);
const exportPlaybackUrl = ref('');
const structureDialog = ref<'project' | 'chapter' | 'scene' | 'rename-project' | 'rename-chapter' | 'rename-scene' | null>(null);
const structureTitle = ref('');
const structureChapter = ref('');
const creationId = ref('');
const pendingDelete = ref<{ kind: 'event' | 'scene' | 'chapter'; id: string; title: string } | null>(null);
const history = ref<{ transactionId: string; sequence: number; summary: string; actor: string; at: string; undoOf?: string }[]>([]);
const uploadKind = ref<'background' | 'character' | 'cg' | 'audio' | 'video'>('background');
const uploadName = ref('');
const uploadFile = ref<File | null>(null);
const levelFile = ref<File | null>(null);
const uploadRequestId = ref('');
const assetPreviews = ref<Record<string, string>>({});
let unsubscribe: (() => void) | null = null;
let settleSubscription: ((ready: boolean) => void) | null = null;

const selectedEvent = computed<SceneEvent | null>(() => scene.value?.events.find((event) => event.eventId === selectedEventId.value) ?? null);
const spec = computed(() => (selectedEvent.value ? EVENT_SPECS[selectedEvent.value.type] : null));
const draftDirty = computed(() => draft.value !== null);
const editableType = computed(() => draftTarget.value?.type ?? selectedEvent.value?.type ?? 'dialogue');
const editableSpec = computed(() => EVENT_SPECS[editableType.value]);
const draftIssues = computed(() => draft.value ? eventFormIssues(editableType.value, draft.value, assets.value, outline.value) : []);
const currentChapter = computed(() => outline.value?.outline.find((chapter) => chapter.chapterId === (scene.value?.scene.chapterId ?? structureChapter.value)) ?? outline.value?.outline[0]);
const projectTitle = computed(() => projects.value.find((project) => project.projectId === projectId.value)?.title ?? '');
const canWrite = computed(() => connected.value && !!projectId.value && !busy.value && !draftDirty.value && !themeDirty.value);

function guardDraft(allowTheme = false): boolean {
  if (!draftDirty.value) {
    if (themeDirty.value && !allowTheme) { activePanel.value = 'theme'; notify('主题有未保存的修改，请先保存主题或取消修改。'); return false; }
    return true;
  }
  draftNotice.value = '你有未保存的草稿。请先提交修改或放弃草稿，再切换或执行其他操作。';
  notify(draftNotice.value);
  return false;
}

function invalidateDelivery(): void {
  exportedProject.value = null;
  exportPlaybackUrl.value = '';
  playbackResult.value = null;
}

function notify(text: string): void {
  message.value = text;
}

async function withErrors<T>(action: () => Promise<T>, context: string): Promise<T | null> {
  pendingRequests.value += 1;
  try {
    return await action();
  } catch (error) {
    const detail = error instanceof LiveApiError ? `${error.code}: ${error.message}` : String(error);
    if (error instanceof TypeError || (error instanceof LiveApiError && ['BACKEND_UNAVAILABLE', 'UI_SESSION_FAILED'].includes(error.code))) connected.value = false;
    notify(`${context}失败 — ${detail}`);
    return null;
  } finally {
    pendingRequests.value -= 1;
  }
}

// ---- 变量 / 角色 / 关卡面板：与 AI 工具走完全相同的后台事务入口 ----
const operationAttempts = new Map<string, { requestId: string; baseRevision: string }>();
async function submitOperations(context: string, operations: { action: string; payload: Record<string, unknown>; targetIds?: string[] }[], baseRevision = revision.value): Promise<boolean> {
  if (!projectId.value) { notify('请先新建或打开作品。'); return false; }
  if (!guardDraft(context === '修改主题')) return false;
  const targetProject = projectId.value;
  const attemptKey = JSON.stringify([targetProject, context, operations]);
  const attempt = operationAttempts.get(attemptKey) ?? { requestId: `ui-operation-${crypto.randomUUID()}`, baseRevision };
  operationAttempts.set(attemptKey, attempt);
  const outcome = await withErrors(
    () => api.transaction(targetProject, {
      baseRevision: attempt.baseRevision,
      requestId: attempt.requestId,
      operations,
      summary: context,
    }),
    context,
  );
  if (!outcome) return false;
  operationAttempts.delete(attemptKey);
  if (projectId.value !== targetProject) return false;
  if (outcome.status !== 'applied') {
    notify(`${context}被拒绝 — ${outcome.issues.map((issue) => issue.message).join('；') || outcome.status}`);
    return false;
  }
  lastUndo.value = outcome.undoId ? { undoId: outcome.undoId, summary: outcome.summary ?? context } : lastUndo.value;
  invalidateDelivery();
  notify(`${context}完成（修订 ${outcome.revision}）`);
  await refreshOutline();
  return true;
}

function openStructureDialog(kind: NonNullable<typeof structureDialog.value>, chapterId?: string): void {
  if (!guardDraft()) return;
  structureDialog.value = kind;
  structureChapter.value = chapterId ?? currentChapter.value?.chapterId ?? '';
  structureTitle.value = kind === 'rename-project' ? projectTitle.value : kind === 'rename-scene' ? scene.value?.scene.title ?? '' : kind === 'rename-chapter' ? currentChapter.value?.title ?? '' : '';
  creationId.value = `prj_${crypto.randomUUID().replace(/-/g, '').slice(0, 20)}`;
}

async function saveStructure(): Promise<void> {
  const title = structureTitle.value.trim();
  if (!title) { notify('请填写名称。'); return; }
  const kind = structureDialog.value;
  if (kind === 'project') {
    const id = creationId.value;
    let result = await withErrors(() => api.createProject({ title, projectId: id }), '新建作品');
    if (!result) {
      // A retry after an unknown network result recovers this same creation ID.
      await refreshProjects();
      if (!projects.value.some((project) => project.projectId === id)) return;
      result = { projectId: id, revision: '', chapterId: '', sceneId: '' };
    }
    if (!result) return;
    structureDialog.value = null;
    await refreshProjects();
    await switchProject(result.projectId);
    notify('作品已创建。可以添加对白，或先导入背景与立绘。');
    return;
  }
  const action = kind === 'chapter' ? 'chapter.create' : kind === 'scene' ? 'scene.create' : kind === 'rename-project' ? 'project.update' : kind === 'rename-chapter' ? 'chapter.update' : 'scene.update';
  const payload: Record<string, unknown> = { title };
  if (kind === 'scene' || kind === 'rename-chapter') payload.chapterId = structureChapter.value;
  if (kind === 'rename-scene') payload.sceneId = sceneId.value;
  if (kind === 'rename-project') payload.projectId = projectId.value;
  if (await submitOperations('保存作品结构', [{ action, payload }])) {
    structureDialog.value = null;
    await refreshProjects();
  }
}

async function chooseScene(id: string): Promise<void> {
  if (!guardDraft()) return;
  pendingDelete.value = null;
  sceneId.value = id;
  selectedEventId.value = '';
  await refreshScene();
}

async function moveEvent(offset: number): Promise<void> {
  if (!selectedEvent.value) return;
  const index = scene.value?.events.findIndex((event) => event.eventId === selectedEventId.value) ?? -1;
  await submitOperations('调整事件顺序', [{ action: 'event.move', payload: { sceneId: sceneId.value, eventId: selectedEventId.value, index: index + offset } }]);
}

async function moveScene(offset: number): Promise<void> {
  const chapter = currentChapter.value;
  if (!chapter) return;
  const index = chapter.scenes.findIndex((item) => item.sceneId === sceneId.value);
  await submitOperations('调整场景顺序', [{ action: 'scene.move', payload: { sceneId: sceneId.value, chapterId: chapter.chapterId, index: index + offset } }]);
}

async function moveChapter(offset: number): Promise<void> {
  const chapters = outline.value?.outline ?? [];
  const index = chapters.findIndex((chapter) => chapter.chapterId === currentChapter.value?.chapterId);
  const adjacent = chapters[index + offset];
  if (!adjacent || index < 0) return;
  await submitOperations('调整章节顺序', [
    { action: 'chapter.update', payload: { chapterId: chapters[index].chapterId, order: index + offset } },
    { action: 'chapter.update', payload: { chapterId: adjacent.chapterId, order: index } },
  ]);
}

async function confirmDelete(): Promise<void> {
  const target = pendingDelete.value;
  if (!target || !guardDraft()) return;
  const payload = target.kind === 'event' ? { sceneId: sceneId.value, eventId: target.id } : target.kind === 'scene' ? { sceneId: target.id } : { chapterId: target.id };
  if (await submitOperations(`删除${target.title}`, [{ action: `${target.kind}.delete`, payload }])) {
    pendingDelete.value = null;
    if (target.kind === 'event') selectedEventId.value = '';
    await refreshOutline();
  }
}

function pickFile(event: Event, level = false): void {
  const file = (event.target as HTMLInputElement).files?.[0] ?? null;
  if (level) levelFile.value = file;
  else { uploadFile.value = file; if (file) uploadName.value = file.name.replace(/\.[^.]+$/, ''); }
  uploadRequestId.value = `ui-upload-${crypto.randomUUID()}`;
}

async function uploadSelectedFile(level = false): Promise<void> {
  if (!guardDraft() || !projectId.value) return;
  const file = level ? levelFile.value : uploadFile.value;
  const name = level ? newLevel.value.title.trim() : uploadName.value.trim();
  if (!file || !name) { notify('请填写名称并选择文件。'); return; }
  if (file.size > 24 * 1024 * 1024) { notify('单个文件不能超过 24 MB。'); return; }
  const targetProject = projectId.value;
  const baseRevision = revision.value;
  const kind = level ? 'level' : uploadKind.value;
  const attempt = uploadRequestId.value;
  const result = await withErrors(async () => {
    const dataBase64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1]);
      reader.onerror = () => reject(reader.error ?? new Error('无法读取文件，请重新选择。'));
      reader.readAsDataURL(file);
    });
    const hash = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify({ kind, name, fileName: file.name, dataBase64 }))));
    const fingerprint = Array.from(hash, (byte) => byte.toString(16).padStart(2, '0')).join('');
    return api.upload(targetProject, { baseRevision, requestId: `${attempt}-${fingerprint}`, kind, name, fileName: file.name, dataBase64 });
  }, level ? '安装关卡' : '导入素材');
  if (!result) return;
  if (projectId.value !== targetProject) return;
  if (result.status !== 'applied') { notify(`导入未生效：${result.issues[0]?.message ?? result.status}`); return; }
  lastUndo.value = result.undoId ? { undoId: result.undoId, summary: result.summary ?? '' } : null;
  invalidateDelivery();
  uploadRequestId.value = `ui-upload-${crypto.randomUUID()}`;
  notify(level ? '关卡程序已安装，可添加 HTML 关卡事件。' : '素材已导入，可以按名称选择并添加到剧情。');
  await refreshOutline();
}

async function refreshHistory(): Promise<void> {
  if (!projectId.value) return;
  const requestedProject = projectId.value;
  const result = await withErrors(() => api.history(requestedProject), '读取历史');
  if (result && projectId.value === requestedProject) history.value = result.transactions.slice().reverse();
}

async function undoHistory(transactionId: string): Promise<void> {
  if (!guardDraft()) return;
  const outcome = await withErrors(() => api.undo(projectId.value, transactionId, revision.value), '撤销历史修改');
  if (!outcome) return;
  if (outcome.status !== 'applied') { notify(`撤销未生效：${outcome.issues[0]?.message ?? outcome.status}`); return; }
  invalidateDelivery();
  await refreshOutline();
  await refreshHistory();
  notify('已撤销这次修改。');
}

function assetKindLabel(kind: string): string {
  return ({ background: '背景', character: '人物立绘', cg: '插画', audio: '音频', video: '视频' } as Record<string, string>)[kind] ?? kind;
}

function displayParam(key: string, value: unknown): string {
  const id = String(value ?? '');
  if (['assetId', 'characterId'].includes(key)) return assets.value.find((asset) => asset.assetId === id)?.name ?? (id || '未设置');
  if (key === 'levelId') return outline.value?.levels.find((level) => level.levelId === id)?.title ?? (id || '未设置');
  if (key === 'variableId') return outline.value?.variables.find((variable) => variable.variableId === id)?.name ?? (id || '未设置');
  if (key === 'targetSceneId') return outline.value?.outline.flatMap((chapter) => chapter.scenes).find((item) => item.sceneId === id)?.title ?? (id || '未设置');
  if (Array.isArray(value)) return value.map((option) => `${option.label ?? ''} → ${displayParam('targetSceneId', option.targetSceneId)}`).join('\n');
  if (value === true) return '是';
  if (value === false) return '否';
  return asText(value) || '未设置';
}

function displayEventSummary(event: SceneEvent): string {
  const params = event.params;
  const asset = displayParam('assetId', params.assetId);
  if (event.type === 'background') return asset;
  if (event.type === 'character') return `${asset} · ${({ enter: '入场', exit: '退场', move: '移动' } as Record<string, string>)[String(params.action)] ?? ''} · ${({ left: '左侧', center: '中央', right: '右侧' } as Record<string, string>)[String(params.position)] ?? ''}`;
  if (event.type === 'variable') return `${displayParam('variableId', params.variableId)} ${params.op === 'add' ? '增加' : '设为'} ${String(params.value)}`;
  if (event.type === 'level') return displayParam('levelId', params.levelId);
  if (event.type === 'jump') return `前往 ${params.targetSceneId ? displayParam('targetSceneId', params.targetSceneId) : scene.value?.events.find((item) => item.eventId === params.targetEventId)?.summary ?? '未设置目标'}`;
  if (event.type === 'audio') return `${asset} · ${({ play: '播放', stop: '停止', fade: '淡出' } as Record<string, string>)[String(params.action)] ?? ''}`;
  if (event.type === 'video') return asset;
  return event.summary;
}

function downloadDraft(): void {
  if (!draft.value || !draftTarget.value) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify({ target: draftTarget.value, params: draft.value }, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = '拾光-未保存草稿.json';
  link.click();
  URL.revokeObjectURL(url);
}

function protectDraftOnExit(event: BeforeUnloadEvent): void {
  if (draftDirty.value || themeDirty.value) { event.preventDefault(); event.returnValue = ''; }
}

async function openPlayback(fromStart = false, exported = false): Promise<void> {
  if (!guardDraft() || !projectId.value) return;
  const result = await withErrors(() => exported && exportedProject.value ? api.exportPlayback(projectId.value, exportedProject.value.exportId) : api.playback(projectId.value, fromStart ? undefined : sceneId.value), '打开试玩');
  if (!result) return;
  if (exported) exportPlaybackUrl.value = result.url;
  else playbackResult.value = result;
}

const newVariable = ref<{ name: string; type: 'boolean' | 'number' | 'string'; initial: string }>({ name: '', type: 'boolean', initial: 'false' });
const newCharacter = ref<{ name: string; description: string }>({ name: '', description: '' });
const newLevel = ref<{ title: string }>({ title: '' });

function variableInitialValue(): unknown {
  if (newVariable.value.type === 'boolean') return newVariable.value.initial === 'true';
  if (newVariable.value.type === 'number') return Number(newVariable.value.initial || '0');
  return newVariable.value.initial;
}


const themeDraft = ref<{ textColor: string; fontSize: number; align: 'left' | 'center'; accentColor: string }>({ textColor: '#e8eef7', fontSize: 18, align: 'left', accentColor: '#7fb2ff' });
const themeSavedValue = ref('');
const themeBaseRevision = ref('');
const themeDirty = computed(() => !!themeSavedValue.value && JSON.stringify(themeDraft.value) !== themeSavedValue.value);

function syncThemeDraft(force = false): void {
  if (themeDirty.value && !force) return;
  const theme = outline.value?.theme;
  if (!theme) return;
  themeDraft.value = {
    textColor: theme.dialogue.textColor,
    fontSize: theme.dialogue.fontSize,
    align: theme.dialogue.align,
    accentColor: theme.menu.accentColor,
  };
  themeSavedValue.value = JSON.stringify(themeDraft.value);
  themeBaseRevision.value = revision.value;
}

async function saveTheme(): Promise<void> {
  const ok = await submitOperations('修改主题', [{
    action: 'theme.update',
    targetIds: ['thm_default'],
    payload: {
      textColor: themeDraft.value.textColor,
      fontSize: Number(themeDraft.value.fontSize),
      align: themeDraft.value.align,
      accentColor: themeDraft.value.accentColor,
    },
  }], themeBaseRevision.value);
  if (ok) syncThemeDraft(true);
}


// ---- 生成模板（recipes）：给图片/视频生成预设提示词与参数 ----
const newRecipe = ref<{ name: string; taskType: 'image.text' | 'image.edit' | 'video.text' | 'video.image'; prompt: string }>({ name: '', taskType: 'image.text', prompt: '' });

async function createRecipe(): Promise<void> {
  if (!newRecipe.value.name.trim() || !newRecipe.value.prompt.trim()) { notify('请填写模板名与提示词'); return; }
  const ok = await submitOperations('新建生成模板', [{
    action: 'recipe.create',
    payload: { name: newRecipe.value.name.trim(), taskType: newRecipe.value.taskType, prompt: newRecipe.value.prompt.trim() },
  }]);
  if (ok) {
    newRecipe.value = { name: '', taskType: newRecipe.value.taskType, prompt: '' };
    activePanel.value = 'recipes';
  }
}

async function updateRecipePrompt(recipe: { recipeId: string; name: string; taskType: string; prompt: string }, prompt: string, input?: HTMLInputElement): Promise<void> {
  if (!prompt.trim()) { if (input) input.value = recipe.prompt; notify('模板提示词不能为空，未保存此次修改。'); await refreshOutline(); return; }
  await submitOperations('修改生成模板', [{
    action: 'recipe.update',
    targetIds: [recipe.recipeId],
    payload: { recipeId: recipe.recipeId, prompt: prompt.trim() },
  }]);
}

async function deleteRecipe(recipe: { recipeId: string }): Promise<void> {
  await submitOperations('删除生成模板', [{ action: 'recipe.delete', targetIds: [recipe.recipeId], payload: { recipeId: recipe.recipeId } }]);
}

async function createVariable(): Promise<void> {
  if (!newVariable.value.name.trim()) { notify('请填写变量名'); return; }
  const ok = await submitOperations('新建变量', [{
    action: 'variable.create',
    payload: { name: newVariable.value.name.trim(), type: newVariable.value.type, initial: variableInitialValue() },
  }]);
  if (ok) {
    newVariable.value = { name: '', type: newVariable.value.type, initial: newVariable.value.initial };
    activePanel.value = 'variables';
  }
}

async function updateVariableInitial(variable: { variableId: string; name: string; type: string; initial: unknown }, raw: string): Promise<void> {
  const initial = variable.type === 'boolean' ? raw === 'true' : variable.type === 'number' ? Number(raw || '0') : raw;
  await submitOperations('修改变量初值', [{ action: 'variable.update', targetIds: [variable.variableId], payload: { variableId: variable.variableId, initial } }]);
}

async function deleteVariable(variable: { variableId: string }): Promise<void> {
  await submitOperations('删除变量', [{ action: 'variable.delete', targetIds: [variable.variableId], payload: { variableId: variable.variableId } }]);
}

async function createCharacter(): Promise<void> {
  if (!newCharacter.value.name.trim()) { notify('请填写角色名'); return; }
  const ok = await submitOperations('新建角色', [{
    action: 'character.create',
    payload: { name: newCharacter.value.name.trim(), description: newCharacter.value.description.trim() },
  }]);
  if (ok) {
    newCharacter.value = { name: '', description: '' };
    activePanel.value = 'characters';
  }
}

async function renameCharacter(character: { characterId: string; name: string }, name: string, input?: HTMLInputElement): Promise<void> {
  if (!name.trim()) { if (input) input.value = character.name; notify('角色名不能为空，未保存此次修改。'); await refreshOutline(); return; }
  await submitOperations('重命名角色', [{ action: 'character.update', targetIds: [character.characterId], payload: { characterId: character.characterId, name: name.trim() } }]);
}

async function deleteCharacter(character: { characterId: string }): Promise<void> {
  await submitOperations('删除角色', [{ action: 'character.delete', targetIds: [character.characterId], payload: { characterId: character.characterId } }]);
}

async function deleteLevel(level: { levelId: string }): Promise<void> {
  await submitOperations('删除关卡', [{ action: 'level.delete', targetIds: [level.levelId], payload: { levelId: level.levelId } }]);
}

async function refreshProjects(): Promise<void> {
  const result = await withErrors(() => api.listProjects(), '读取工程列表');
  if (!result) return;
  projects.value = result.projects;
  if (!projectId.value && result.projects.length) projectId.value = result.projects[0].projectId;
}

async function refreshWorkspace(): Promise<void> {
  if (!guardDraft()) return;
  await refreshProjects();
  watchChanges();
  await refreshOutline();
  if (activePanel.value === 'history') await refreshHistory();
}

async function exportCurrentProject(): Promise<void> {
  if (!projectId.value || !guardDraft()) return;
  const requestedProject = projectId.value;
  const result = await withErrors(async () => {
    const validation = await api.validate(requestedProject);
    if (!validation.ok) throw new LiveApiError('VALIDATION_FAILED', validation.issues[0]?.message ?? '请先修正工程中的校验问题');
    const exported = await api.exportProject(requestedProject);
    if (!exported.playerIncluded || exported.missingAssets.length || exported.missingLevels?.length || exported.sensitiveHits.length) {
      throw new LiveApiError('EXPORT_INCOMPLETE', '导出存在缺失素材、敏感文件或展示器缺失，请检查工程');
    }
    return exported;
  }, '导出作品');
  if (result && requestedProject === projectId.value) {
    exportedProject.value = result;
    exportPlaybackUrl.value = '';
    notify(`导出完成（修订 ${result.revision}，${result.fileCount} 个文件）`);
  }
}

async function refreshOutline(): Promise<void> {
  if (!projectId.value) { validationState.value = 'idle'; return; }
  const requestedProject = projectId.value;
  const generation = ++outlineGeneration;
  const result = await withErrors(() => api.outline(requestedProject), '读取工程结构');
  if (!result || projectId.value !== requestedProject || generation !== outlineGeneration || isOlderRevision(result.revision)) return;
  outline.value = result;
  revision.value = result.revision;
  syncThemeDraft();
  const firstScene = result.outline[0]?.scenes[0]?.sceneId ?? '';
  if (!draftDirty.value && (!sceneId.value || !result.outline.some((chapter) => chapter.scenes.some((item) => item.sceneId === sceneId.value)))) {
    sceneId.value = firstScene;
  }
  const assetList = await withErrors(() => api.assets(requestedProject), '读取素材');
  if (projectId.value !== requestedProject || generation !== outlineGeneration) return;
  if (assetList) {
    assets.value = assetList.assets;
    for (const asset of assetList.assets) {
      if (!['background', 'character', 'cg'].includes(asset.kind) || assetPreviews.value[asset.assetId]) continue;
      void api.assetBlob(requestedProject, asset.assetId).then((blob) => {
        if (projectId.value === requestedProject) assetPreviews.value = { ...assetPreviews.value, [asset.assetId]: URL.createObjectURL(blob) };
      }).catch(() => {});
    }
  }
  await refreshScene();
  await refreshValidation();
}

async function refreshScene(): Promise<void> {
  if (!projectId.value || !sceneId.value) return;
  const requestedProject = projectId.value;
  const requestedScene = sceneId.value;
  const generation = ++sceneGeneration;
  const result = await withErrors(() => api.scene(requestedProject, requestedScene), '读取场景');
  if (!result || projectId.value !== requestedProject || sceneId.value !== requestedScene || generation !== sceneGeneration || isOlderRevision(result.revision)) return;
  scene.value = result;
  revision.value = result.revision;
  if (!draftDirty.value && !result.events.some((event) => event.eventId === selectedEventId.value)) {
    selectedEventId.value = result.events[0]?.eventId ?? '';
  }
}

async function refreshValidation(): Promise<void> {
  if (!projectId.value) { validationState.value = 'idle'; return; }
  const requestedProject = projectId.value;
  const generation = ++validationGeneration;
  validationState.value = 'checking';
  const result = await withErrors(() => api.validate(requestedProject), '校验工程');
  if (projectId.value !== requestedProject || generation !== validationGeneration) return;
  if (!result) { validationState.value = 'unavailable'; return; }
  if (isOlderRevision(result.revision)) { validationState.value = 'idle'; return; }
  issues.value = result.issues;
  validationState.value = result.ok ? 'passed' : 'failed';
}

function isOlderRevision(next: string): boolean {
  return /^\d+$/.test(next) && /^\d+$/.test(revision.value) && BigInt(next) < BigInt(revision.value);
}

async function refreshAiSessions(): Promise<void> {
  const result = await withErrors(() => api.aiSessions(), '读取 AI 会话');
  if (result) aiSessions.value = result.sessions;
}

function selectEvent(event: SceneEvent): void {
  if (selectedEventId.value === event.eventId) return;
  if (!guardDraft()) return;
  selectedEventId.value = event.eventId;
  pendingDelete.value = null;
}

function startEdit(): void {
  if (!selectedEvent.value || !guardDraft()) return;
  draft.value = JSON.parse(JSON.stringify(selectedEvent.value.params)) as Record<string, unknown>;
  draftTarget.value = { mode: 'edit', projectId: projectId.value, sceneId: sceneId.value, eventId: selectedEvent.value.eventId, type: selectedEvent.value.type, baseRevision: revision.value };
  draftNotice.value = '草稿未提交：后台变化不会覆盖你的输入，提交时会重新检查基础版本。';
}

async function cancelEdit(): Promise<void> {
  draft.value = null;
  draftTarget.value = null;
  draftNotice.value = '';
  await refreshOutline();
}

function asText(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

async function submitDraft(): Promise<void> {
  if (busy.value) return;
  const target = draftTarget.value;
  if (!target || !draft.value || target.projectId !== projectId.value || target.sceneId !== sceneId.value) return;
  if (draftIssues.value.length) { notify(`请完成表单：${draftIssues.value[0].message}`); return; }
  const params = JSON.parse(JSON.stringify(draft.value)) as Record<string, unknown>;
  const result = await withErrors(async () => {
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(params))));
    const fingerprint = Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('');
    const requestId = `ui-event-${target.mode}-${target.eventId}-${target.baseRevision}-${fingerprint}`;
    return api.transaction(target.projectId, {
      baseRevision: target.baseRevision, requestId,
      summary: target.mode === 'create' ? `添加${EVENT_SPECS[target.type].label}` : `人工修改事件 ${target.eventId}`,
      operations: target.mode === 'create' ? [{ action: 'event.insert', payload: { sceneId: target.sceneId, eventId: target.eventId, type: target.type, params, index: target.index } }] : [{ action: 'event.update', targetIds: [target.eventId!], payload: { sceneId: target.sceneId, eventId: target.eventId, params } }],
    });
  }, '提交修改');
  if (!result) return;
  if (draftTarget.value !== target) return;
  if (result.status !== 'applied') {
    notify(`提交未生效（${result.errorCode ?? result.status}）：${result.issues[0]?.message ?? '请重新读取后再试'}`);
    if (result.status === 'conflict') { draftNotice.value = '后台内容已变化。草稿已保留，请先放弃草稿并重新读取，再合并需要的修改。'; await refreshOutline(); }
    return;
  }
  revision.value = result.revision;
  lastUndo.value = result.undoId ? { undoId: result.undoId, summary: result.summary ?? '' } : null;
  draft.value = null;
  draftTarget.value = null;
  draftNotice.value = '';
  invalidateDelivery();
  notify(`${target.mode === 'create' ? '已插入事件' : '已保存'}（修订 ${result.revision}）`);
  await refreshOutline();
  if (target.mode === 'create') selectedEventId.value = result.changedIds.find((id) => id.startsWith('ev_')) ?? selectedEventId.value;
}

async function insertEvent(): Promise<void> {
  if (!projectId.value || !sceneId.value || !guardDraft()) return;
  const type = insertType.value;
  const params = defaultEventParams(type);
  if (type === 'dialogue') {
    params.speaker = '';
    params.text = '新的对白（人工插入）';
  }
  const selectedIndex = scene.value?.events.findIndex((event) => event.eventId === selectedEventId.value) ?? -1;
  draftTarget.value = { mode: 'create', projectId: projectId.value, sceneId: sceneId.value, eventId: `ev_${crypto.randomUUID().replace(/-/g, '').slice(0, 20)}`, type, baseRevision: revision.value, index: selectedIndex < 0 ? scene.value?.events.length ?? 0 : selectedIndex + 1 };
  draft.value = params;
  draftNotice.value = '填写参数后点击“添加事件”。尚未写入作品。';
}

async function deleteSelected(): Promise<void> {
  if (!projectId.value || !sceneId.value || !selectedEvent.value || !guardDraft()) return;
  pendingDelete.value = { kind: 'event', id: selectedEvent.value.eventId, title: EVENT_SPECS[selectedEvent.value.type].label };
}

async function undoLast(): Promise<void> {
  if (!projectId.value || !lastUndo.value || !guardDraft()) return;
  const result = await withErrors(() => api.undo(projectId.value, lastUndo.value!.undoId, revision.value), '撤销');
  if (!result) return;
  if (result.status !== 'applied') {
    notify(`撤销未生效（${result.errorCode ?? result.status}）：${result.issues[0]?.message ?? ''}`);
    return;
  }
  revision.value = result.revision;
  notify(`已撤销上一次提交（修订 ${result.revision}）`);
  lastUndo.value = null;
  invalidateDelivery();
  await refreshOutline();
}

async function toggleAiSession(session: { sessionId: string; paused: boolean }): Promise<void> {
  await withErrors(() => api.setAiSessionPaused(session.sessionId, !session.paused), '切换 AI 写入状态');
  await refreshAiSessions();
}

async function switchProject(id: string): Promise<void> {
  if (!guardDraft()) return;
  projectLoading.value = true;
  try {
    projectId.value = id;
    exportedProject.value = null;
    sceneId.value = '';
    selectedEventId.value = '';
    draft.value = null;
    draftTarget.value = null;
    draftNotice.value = '';
    outline.value = null;
    scene.value = null;
    assets.value = [];
    issues.value = [];
    themeSavedValue.value = '';
    changeLog.value = [];
    history.value = [];
    lastUndo.value = null;
    revision.value = '';
    validationState.value = 'idle';
    pendingDelete.value = null;
    invalidateDelivery();
    Object.values(assetPreviews.value).forEach((url) => URL.revokeObjectURL(url));
    assetPreviews.value = {};
    const subscriptionReady = watchChanges();
    await refreshOutline();
    await subscriptionReady;
  } finally {
    projectLoading.value = false;
  }
}

function onProjectChange(event: Event): void {
  const element = event.target as HTMLSelectElement;
  const id = element.value;
  element.value = projectId.value;
  void switchProject(id);
}

async function connect(): Promise<void> {
  if (initializing.value) return;
  initializing.value = true;
  fatal.value = '';
  try {
    // Portable services may receive a new API port after restart. Discover it
    // through the same editor origin, without passing session credentials.
    const discovery = await fetch('/__studio/discovery', { cache: 'no-store' }).catch(() => null);
    if (discovery?.ok && discovery.headers.get('content-type')?.includes('application/json')) {
      const body = await discovery.json() as { apiUrl?: string };
      if (body.apiUrl && body.apiUrl !== api.baseUrl) { unsubscribe?.(); unsubscribe = null; api.setBaseUrl(body.apiUrl); apiBase.value = api.baseUrl; }
    }
    await api.bootstrap();
    const status = await api.health();
    health.value = { serviceVersion: status.serviceVersion, pid: status.pid };
    connected.value = true;
    await refreshProjects();
    await refreshAiSessions();
    if (!draftDirty.value) await refreshOutline();
    await watchChanges();
  } catch (error) {
    connected.value = false;
    fatal.value = error instanceof LiveApiError ? `${error.code}: ${error.message}` : String(error);
  } finally {
    initializing.value = false;
  }
}

function watchChanges(): Promise<boolean> {
  settleSubscription?.(false);
  settleSubscription = null;
  unsubscribe?.();
  unsubscribe = null;
  const subscribedProject = projectId.value;
  if (!subscribedProject) return Promise.resolve(true);
  let settleReady: (ready: boolean) => void = () => {};
  const ready = new Promise<boolean>((resolve) => { settleReady = resolve; });
  settleSubscription = settleReady;
  unsubscribe = api.subscribe(subscribedProject, (event) => {
    if (projectId.value !== subscribedProject) return;
    changeLog.value = [{ at: new Date().toLocaleTimeString(), actor: event.actor, summary: event.summary, changedIds: event.changedIds }, ...changeLog.value].slice(0, 20);
    invalidateDelivery();
    if (draftDirty.value) {
      draftNotice.value = `后台已提交新修订 ${event.revision}（${event.actor}：${event.summary}）。你的草稿仍保留；提交时会重新检查基础版本。`;
      return;
    }
    void refreshOutline();
  }, () => {
    connected.value = false;
    fatal.value = '连接已中断。草稿仍保留，请重新连接后核对作品。';
    settleReady(false);
  }, () => {
    // Opening the subscription and re-reading afterwards closes the initial snapshot gap.
    // Keep initialization locked until that complete read finishes so inputs cannot be
    // enabled between its outline/assets/scene/validation requests.
    void (async () => {
      if (projectId.value === subscribedProject && !draftDirty.value) await refreshOutline();
      settleReady(projectId.value === subscribedProject);
    })();
  });
  return ready;
}

onMounted(() => { void connect(); window.addEventListener('beforeunload', protectDraftOnExit); });
onUnmounted(() => { settleSubscription?.(false); unsubscribe?.(); Object.values(assetPreviews.value).forEach((url) => URL.revokeObjectURL(url)); window.removeEventListener('beforeunload', protectDraftOnExit); });

function eventLabel(event: SceneEvent): string {
  return `${EVENT_SPECS[event.type]?.label ?? event.type}｜${event.summary}`;
}
</script>

<template>
  <div class="live-app">
    <header class="toolbar">
      <strong>拾光 Studio · 作品编辑器</strong>
      <span v-if="initializing" class="badge" data-testid="connection">正在连接…</span>
      <span v-else-if="connected" class="badge ok" data-testid="connection">后台已连接</span>
      <span v-else class="badge bad" data-testid="connection">连接已断开</span>
      <span class="badge" data-testid="revision">{{ draftDirty || themeDirty ? '有未保存草稿' : projectId ? '修改已保存' : '尚未打开作品' }} · 修订 {{ revision || '—' }}</span>
      <span class="spacer" />
      <button data-testid="new-project" :disabled="busy || !connected || draftDirty" @click="openStructureDialog('project')">新建作品</button>
      <button data-testid="preview-scene" :disabled="!canWrite || !sceneId" @click="openPlayback()">试玩当前场景</button>
      <button data-testid="preview-project" :disabled="!canWrite" @click="openPlayback(true)">从头试玩</button>
      <button data-testid="connect" :disabled="busy" @click="connect">重新连接</button>
      <button data-testid="undo" :disabled="!canWrite || !lastUndo" @click="undoLast">撤销上一次修改</button>
      <button data-testid="export-project" :disabled="!canWrite" :title="draftDirty ? '请先提交或放弃草稿' : '导出已保存的作品'" @click="exportCurrentProject">导出作品</button>
    </header>

    <p v-if="fatal" class="fatal" data-testid="fatal">
      {{ fatal }} 请使用作品包中的“启动编辑器.cmd”恢复服务，再点“重新连接”。未保存的草稿仍留在本页。
    </p>
    <p v-if="message" class="message" data-testid="message">{{ message }}</p>
    <section v-if="connected && !initializing && !projects.length" class="welcome" data-testid="welcome">
      <span class="eyebrow">从你的故事开始</span>
      <h1>创建第一部作品</h1>
      <p>无需 Demo 或预先准备的素材。新建作品后即可写对白，再添加背景、立绘与分支。</p>
      <button class="primary" data-testid="welcome-create" @click="openStructureDialog('project')">新建作品</button>
      <p class="muted">已有作品会显示在本工作区的作品列表中。外部工具新建作品后，点击“刷新作品列表”。</p>
      <button data-testid="empty-refresh" :disabled="busy" @click="refreshWorkspace">刷新作品列表</button>
    </section>
    <section v-if="playbackResult" class="message" data-testid="playback-result">
      <a :href="playbackResult.url" target="_blank" rel="noopener" data-testid="playback-link">打开试玩窗口</a>
      <span> · 修订 {{ playbackResult.revision }}。试玩使用已保存内容；后续修改请重新打开。</span>
    </section>
    <section v-if="exportedProject" class="message" data-testid="export-result">
      <strong>作品已导出</strong>
      <p>导出目录（相对于工作区）：<code data-testid="export-directory">{{ exportedProject.directory }}</code></p>
      <p v-if="exportedProject.portableIncluded">已包含展示器与运行环境。将整个导出目录交付，接收方解压后双击“启动展示器.cmd”。</p>
      <p v-else>已导出作品与展示器，本次未附带便携运行环境。请通过本机试玩确认内容；此目录需要 Node 环境运行。</p>
      <button data-testid="preview-export" :disabled="busy" @click="openPlayback(true, true)">验证导出作品</button>
      <button data-testid="open-export-directory" :disabled="busy" @click="withErrors(() => api.openExportDirectory(projectId, exportedProject!.exportId), '打开导出目录')">打开导出目录</button>
      <a v-if="exportPlaybackUrl" :href="exportPlaybackUrl" target="_blank" rel="noopener" data-testid="export-playback-link">打开导出作品</a>
    </section>

    <div v-if="structureDialog" class="dialog-backdrop">
      <form class="dialog" role="dialog" aria-modal="true" aria-labelledby="structure-title" @submit.prevent="saveStructure">
        <h2 id="structure-title">{{ structureDialog === 'project' ? '新建作品' : structureDialog === 'chapter' ? '新建章节' : structureDialog === 'scene' ? '新建场景' : '重命名' }}</h2>
        <label for="structure-name">名称</label>
        <input id="structure-name" v-model="structureTitle" data-testid="structure-name" type="text" maxlength="120" required autofocus :disabled="busy" />
        <template v-if="structureDialog === 'scene'">
          <label for="structure-chapter">所属章节</label>
          <select id="structure-chapter" v-model="structureChapter" data-testid="structure-chapter" required>
            <option v-for="chapter in outline?.outline ?? []" :key="chapter.chapterId" :value="chapter.chapterId">{{ chapter.title }}</option>
          </select>
        </template>
        <p v-if="structureDialog === 'project'" class="muted">自动建立第一章与开场场景。作品会自动保存到本工作区。</p>
        <div class="actions"><button class="primary" data-testid="structure-save" :disabled="busy">创建 / 保存</button><button type="button" :disabled="busy" @click="structureDialog = null">取消</button></div>
      </form>
    </div>
    <section v-if="pendingDelete" class="notice" data-testid="delete-confirmation">
      <p>确认删除“{{ pendingDelete.title }}”？有关联引用时会拒绝删除；删除成功后可撤销。</p>
      <button data-testid="confirm-delete" :disabled="busy" @click="confirmDelete">确认删除</button>
      <button :disabled="busy" @click="pendingDelete = null">取消</button>
    </section>
    <main v-if="projectId" class="layout">
      <aside class="panel">
        <h2>作品</h2>
        <select :value="projectId" data-testid="project-select" :disabled="busy" @change="onProjectChange">
          <option v-for="project in projects" :key="project.projectId" :value="project.projectId">
            {{ project.title }}
          </option>
        </select>
        <div class="actions"><button data-testid="refresh" :disabled="busy || draftDirty" @click="refreshWorkspace">刷新作品列表</button><button :disabled="!canWrite" @click="openStructureDialog('rename-project')">改名</button></div>
        <h2>章节 / 场景</h2>
        <div class="actions"><button data-testid="new-chapter" :disabled="!canWrite" @click="openStructureDialog('chapter')">新建章节</button><button data-testid="new-scene" :disabled="!canWrite" @click="openStructureDialog('scene')">新建场景</button></div>
        <ul class="tree" data-testid="outline">
          <li v-for="chapter in outline?.outline ?? []" :key="chapter.chapterId">
            <span class="chapter">{{ chapter.title }}</span>
            <button class="small" :disabled="!canWrite" @click="structureChapter = chapter.chapterId; structureTitle = chapter.title; structureDialog = 'rename-chapter'">改名</button>
            <button v-if="!chapter.scenes.length" class="small" :disabled="!canWrite" @click="pendingDelete = { kind: 'chapter', id: chapter.chapterId, title: chapter.title }">删除</button>
            <ul>
              <li v-for="item in chapter.scenes" :key="item.sceneId">
                <button
                  class="scene"
                  :class="{ active: item.sceneId === sceneId }"
                  :data-testid="`scene-${item.sceneId}`"
                  :disabled="busy"
                  @click="chooseScene(item.sceneId)"
                >
                  {{ item.title }}（{{ item.eventCount }} 事件）
                </button>
              </li>
            </ul>
          </li>
        </ul>
        <details v-if="scene" class="structure-tools"><summary>当前场景管理</summary>
          <div class="actions"><button :disabled="!canWrite" @click="openStructureDialog('rename-scene')">改名</button><button :disabled="!canWrite" @click="pendingDelete = { kind: 'scene', id: sceneId, title: scene!.scene.title }">删除</button></div>
          <div class="actions"><button :disabled="!canWrite || currentChapter?.scenes[0]?.sceneId === sceneId" @click="moveScene(-1)">场景上移</button><button :disabled="!canWrite || currentChapter?.scenes.at(-1)?.sceneId === sceneId" @click="moveScene(1)">场景下移</button></div>
          <label>移动到章节<select :value="scene.scene.chapterId" :disabled="!canWrite" @change="submitOperations('移动场景', [{ action: 'scene.move', payload: { sceneId, chapterId: ($event.target as HTMLSelectElement).value } }])"><option v-for="chapter in outline?.outline ?? []" :key="chapter.chapterId" :value="chapter.chapterId">{{ chapter.title }}</option></select></label>
          <button :disabled="!canWrite" @click="submitOperations('设置开场场景', [{ action: 'project.update', payload: { projectId, entrySceneId: sceneId } }])">将此场景设为作品开场</button>
          <div class="actions"><button :disabled="!canWrite || currentChapter?.chapterId === outline?.outline[0]?.chapterId" @click="moveChapter(-1)">章节上移</button><button :disabled="!canWrite || currentChapter?.chapterId === outline?.outline.at(-1)?.chapterId" @click="moveChapter(1)">章节下移</button></div>
        </details>
      </aside>

      <section class="panel">
        <h2>事件列表 · {{ scene?.scene.title ?? '未选择场景' }}</h2>
        <div class="insert-row">
          <select v-model="insertType" data-testid="insert-type">
            <option v-for="type in EVENT_TYPES" :key="type" :value="type">{{ EVENT_SPECS[type].label }}</option>
          </select>
          <button data-testid="insert-event" :disabled="!canWrite || !sceneId" @click="insertEvent">配置新事件</button>
          <button data-testid="delete-event" :disabled="!canWrite || !selectedEvent" @click="deleteSelected">删除选中</button>
        </div>
        <p v-if="!scene?.events.length" class="empty-state">这个场景还没有内容。选择“对白”并配置新事件，写下第一句话。</p>
        <div v-if="selectedEvent" class="actions"><button data-testid="event-up" :disabled="!canWrite || selectedEvent.index === 0" @click="moveEvent(-1)">上移</button><button data-testid="event-down" :disabled="!canWrite || selectedEvent.index >= (scene?.events.length ?? 0) - 1" @click="moveEvent(1)">下移</button><span class="muted">新事件插在选中事件之后。</span></div>
        <ul class="events" data-testid="event-list">
          <li v-for="event in scene?.events ?? []" :key="event.eventId">
            <button
              class="event"
              :class="{ active: event.eventId === selectedEventId }"
              :data-testid="`event-${event.eventId}`"
              @click="selectEvent(event)"
            >
              <span class="tag">{{ EVENT_SPECS[event.type]?.label ?? event.type }}</span>
              <span class="summary">{{ displayEventSummary(event) }}</span>
              <span class="id">第 {{ event.index + 1 }} 项</span>
            </button>
          </li>
        </ul>
      </section>

      <section class="panel">
        <h2>属性</h2>
        <template v-if="draftDirty && draft">
          <h3>{{ draftTarget?.mode === 'create' ? '添加' : '编辑' }}{{ editableSpec.label }}</h3>
          <p v-if="draftNotice" class="notice" data-testid="draft-notice">{{ draftNotice }}</p>
          <EventParamEditor v-model="draft" :type="editableType" :assets="assets" :outline="outline" :scene="scene" :disabled="busy || !connected" />
          <div class="actions">
            <button class="primary" data-testid="submit-draft" :disabled="busy || !connected" @click="submitDraft">{{ draftTarget?.mode === 'create' ? '添加事件' : '提交修改' }}</button>
            <button data-testid="cancel-draft" :disabled="busy" @click="cancelEdit">放弃草稿</button>
            <button data-testid="download-draft" @click="downloadDraft">下载草稿备份</button>
          </div>
        </template>
        <template v-else-if="selectedEvent">
          <h3>{{ spec?.label }}</h3>
          <dl class="event-details"><template v-for="param in spec?.params ?? []" :key="param.key"><dt>{{ param.label }}</dt><dd>{{ displayParam(param.key, selectedEvent.params[param.key]) }}</dd></template></dl>
          <button data-testid="start-edit" :disabled="busy || !connected || themeDirty" @click="startEdit">编辑该事件</button>
        </template>
        <p v-else class="empty-state">选择事件查看内容，或点击“配置新事件”开始编写。</p>
        <h2>校验</h2>
        <p v-if="validationState !== 'failed'" :class="validationState === 'passed' ? 'ok-text' : 'muted'" data-testid="validation">{{ validationState === 'passed' ? '已保存内容：结构校验通过' : validationState === 'checking' ? '正在校验…' : validationState === 'unavailable' ? '校验未完成，请重新连接后再试。' : '尚未校验作品' }}</p>
        <ul v-else class="issues" data-testid="validation">
          <li v-for="issue in issues" :key="issue.path + issue.message">{{ issue.message }}<small>{{ issue.path }}</small></li>
        </ul>
      </section>

      <aside class="panel">
        <details class="advanced"><summary>协作与连接信息</summary>
        <p class="api" data-testid="api-base">{{ apiBase }} · 服务 {{ health?.serviceVersion }} · pid {{ health?.pid }}</p>
        <h2>后台变更流</h2>
        <ul class="changes" data-testid="change-log">
          <li v-for="(change, index) in changeLog" :key="index">
            {{ change.at }}｜{{ change.actor }}｜{{ change.summary }}
          </li>
        </ul>
        <h2>AI 会话写入</h2>
        <ul class="sessions" data-testid="ai-sessions">
          <li v-for="session in aiSessions" :key="session.sessionId">
            <span>{{ session.actorId }}</span>
            <button :data-testid="`toggle-${session.sessionId}`" :disabled="busy" @click="toggleAiSession(session)">
              {{ session.paused ? '恢复写入' : '暂停写入' }}
            </button>
          </li>
        </ul>
        </details>
        <h2>素材与作品设置</h2>
        <div class="panel-tabs">
          <button data-testid="tab-assets" :class="{ active: activePanel === 'assets' }" @click="activePanel = 'assets'">素材（{{ assets.length }}）</button>
          <button data-testid="tab-variables" :class="{ active: activePanel === 'variables' }" @click="activePanel = 'variables'">变量（{{ outline?.variables.length ?? 0 }}）</button>
          <button data-testid="tab-characters" :class="{ active: activePanel === 'characters' }" @click="activePanel = 'characters'">角色（{{ outline?.characters.length ?? 0 }}）</button>
          <button data-testid="tab-levels" :class="{ active: activePanel === 'levels' }" @click="activePanel = 'levels'">关卡（{{ outline?.levels.length ?? 0 }}）</button>
          <button data-testid="tab-theme" :class="{ active: activePanel === 'theme' }" @click="activePanel = 'theme'">主题</button>
          <button data-testid="tab-recipes" :class="{ active: activePanel === 'recipes' }" @click="activePanel = 'recipes'">生成模板（{{ outline?.recipes?.length ?? 0 }}）</button>
          <button data-testid="tab-history" :class="{ active: activePanel === 'history' }" @click="activePanel = 'history'; refreshHistory()">历史记录</button>
        </div>

        <template v-if="activePanel === 'assets'">
          <p class="muted">导入你的图片、音频或视频，再在事件表单中按名称选择。单个文件最多 24 MB。</p>
          <div class="field"><label for="asset-kind">素材用途</label><select id="asset-kind" v-model="uploadKind" data-testid="asset-kind" :disabled="!canWrite"><option value="background">背景</option><option value="character">人物立绘</option><option value="cg">插画 / CG</option><option value="audio">音频</option><option value="video">视频</option></select></div>
          <div class="field"><label for="asset-file">选择文件</label><input id="asset-file" type="file" data-testid="asset-file" accept=".png,.jpg,.jpeg,.gif,.webp,.wav,.mp3,.mp4,.webm" :disabled="!canWrite" @change="pickFile($event)" /></div>
          <div class="field"><label for="asset-name">素材名称</label><input id="asset-name" v-model="uploadName" type="text" data-testid="asset-name" :disabled="!canWrite" /></div>
          <button data-testid="asset-import" :disabled="!canWrite || !uploadFile || !uploadName.trim()" @click="uploadSelectedFile()">导入素材</button>
          <ul class="asset-list" data-testid="asset-list"><li v-for="asset in assets" :key="asset.assetId"><img v-if="assetPreviews[asset.assetId]" :src="assetPreviews[asset.assetId]" :alt="asset.name" /><strong>{{ asset.name }}</strong><span class="muted">{{ assetKindLabel(asset.kind) }}</span></li></ul>
        </template>
        <template v-else-if="activePanel === 'history'">
          <button :disabled="busy" @click="refreshHistory">刷新历史</button>
          <ul class="changes"><li v-for="item in history" :key="item.transactionId"><strong>{{ item.summary }}</strong><p>{{ item.at }} · {{ item.actor }}</p><button v-if="item.sequence > 0 && !item.undoOf" :disabled="!canWrite" @click="undoHistory(item.transactionId)">撤销这次修改</button></li></ul>
        </template>
        <template v-else-if="activePanel === 'variables'">
          <ul class="changes" data-testid="variable-list">
            <li v-for="variable in outline?.variables ?? []" :key="variable.variableId">
              <span data-testid="variable-name">{{ variable.name }}（{{ variable.type }}）</span>
              <input
                :data-testid="`variable-initial-${variable.variableId}`"
                :value="String(variable.initial)"
                :disabled="busy"
                @change="updateVariableInitial(variable, ($event.target as HTMLInputElement).value)"
              />
              <button :data-testid="`variable-delete-${variable.variableId}`" :disabled="busy" @click="deleteVariable(variable)">删除</button>
            </li>
          </ul>
          <div class="panel-form">
            <input v-model="newVariable.name" data-testid="variable-new-name" placeholder="变量名" :disabled="busy" />
            <select v-model="newVariable.type" data-testid="variable-new-type" :disabled="busy">
              <option value="boolean">boolean</option>
              <option value="number">number</option>
              <option value="string">string</option>
            </select>
            <input v-model="newVariable.initial" data-testid="variable-new-initial" placeholder="初值" :disabled="busy" />
            <button data-testid="variable-create" :disabled="busy" @click="createVariable">新建变量</button>
          </div>
        </template>

        <template v-else-if="activePanel === 'characters'">
          <ul class="changes" data-testid="character-list">
            <li v-for="character in outline?.characters ?? []" :key="character.characterId">
              <input
                :data-testid="`character-name-${character.characterId}`"
                :value="character.name"
                :disabled="busy"
                @change="renameCharacter(character, ($event.target as HTMLInputElement).value, $event.target as HTMLInputElement)"
              />
              <span class="muted">{{ character.characterId }}</span>
              <button :data-testid="`character-delete-${character.characterId}`" :disabled="busy" @click="deleteCharacter(character)">删除</button>
            </li>
          </ul>
          <div class="panel-form">
            <input v-model="newCharacter.name" data-testid="character-new-name" placeholder="角色名" :disabled="busy" />
            <input v-model="newCharacter.description" data-testid="character-new-description" placeholder="说明（可空）" :disabled="busy" />
            <button data-testid="character-create" :disabled="busy" @click="createCharacter">新建角色</button>
          </div>
        </template>



        <template v-else-if="activePanel === 'recipes'">
          <ul class="changes" data-testid="recipe-list">
            <li v-for="recipe in outline?.recipes ?? []" :key="recipe.recipeId">
              <span data-testid="recipe-name">{{ recipe.name }}（{{ recipe.taskType }}）</span>
              <input
                :data-testid="`recipe-prompt-${recipe.recipeId}`"
                :value="recipe.prompt"
                :disabled="busy"
                @change="updateRecipePrompt(recipe, ($event.target as HTMLInputElement).value, $event.target as HTMLInputElement)"
              />
              <button :data-testid="`recipe-delete-${recipe.recipeId}`" :disabled="busy" @click="deleteRecipe(recipe)">删除</button>
            </li>
          </ul>
          <div class="panel-form">
            <input v-model="newRecipe.name" data-testid="recipe-new-name" placeholder="模板名" :disabled="busy" />
            <select v-model="newRecipe.taskType" data-testid="recipe-new-type" :disabled="busy">
              <option value="image.text">image.text</option>
              <option value="image.edit">image.edit</option>
              <option value="video.text">video.text</option>
              <option value="video.image">video.image</option>
            </select>
            <input v-model="newRecipe.prompt" data-testid="recipe-new-prompt" placeholder="提示词" :disabled="busy" />
            <button data-testid="recipe-create" :disabled="busy" @click="createRecipe">新建模板</button>
          </div>
          <p class="muted">模板存在工程 recipes/ 目录，人工与外部 AI 共用同一份生成配置。</p>
        </template>

        <template v-else-if="activePanel === 'theme'">
          <div class="panel-form">
            <label>对白文字颜色
              <input v-model="themeDraft.textColor" data-testid="theme-text-color" placeholder="#rrggbb" :disabled="busy" />
            </label>
            <label>对白字号
              <input v-model.number="themeDraft.fontSize" data-testid="theme-font-size" type="number" min="12" max="48" :disabled="busy" />
            </label>
            <label>对白对齐
              <select v-model="themeDraft.align" data-testid="theme-align" :disabled="busy">
                <option value="left">左对齐</option>
                <option value="center">居中</option>
              </select>
            </label>
            <label>菜单强调色
              <input v-model="themeDraft.accentColor" data-testid="theme-accent" placeholder="#rrggbb" :disabled="busy" />
            </label>
            <button data-testid="theme-save" :disabled="busy" @click="saveTheme">保存主题</button>
            <button v-if="themeDirty" data-testid="theme-cancel" :disabled="busy" @click="syncThemeDraft(true)">取消主题修改</button>
          </div>
          <p class="muted">对白预览：</p>
          <p data-testid="theme-preview" :style="{ color: themeDraft.textColor, fontSize: themeDraft.fontSize + 'px', textAlign: themeDraft.align }">雨点敲在旧塔的铁门上。</p>
          <p class="muted">展示器读取同一份 themes/theme.json，导出发布包后立即生效。</p>
        </template>

        <template v-else-if="activePanel === 'levels'">
          <ul class="changes" data-testid="level-list">
            <li v-for="level in outline?.levels ?? []" :key="level.levelId">
              <span data-testid="level-title">{{ level.title }}</span>
              <span class="muted">{{ level.entry }}</span>
              <button :data-testid="`level-delete-${level.levelId}`" :disabled="busy" @click="deleteLevel(level)">删除</button>
            </li>
          </ul>
          <div class="panel-form">
            <label>关卡名称<input v-model="newLevel.title" type="text" data-testid="level-new-title" placeholder="关卡名称" :disabled="!canWrite" /></label>
            <label>关卡程序<input type="file" data-testid="level-file" accept=".html,.htm" :disabled="!canWrite" @change="pickFile($event, true)" /></label>
            <button data-testid="level-install" :disabled="!canWrite || !levelFile || !newLevel.title.trim()" @click="uploadSelectedFile(true)">安装 HTML 关卡</button>
          </div>
          <p class="muted">请选择自包含 HTML 程序。安装成功后可在事件表单中按关卡名称选择；发布前还需实际试玩验证。</p>
        </template>
      </aside>
    </main>
  </div>
</template>
