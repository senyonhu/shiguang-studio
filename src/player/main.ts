// 独立展示器：加载发布包内容，使用与编辑器预览相同的运行内核推进剧情。
// 支持：标题页、开始/继续、对白推进、选择、变量、背景/人物图层、等待、跳转、结束、
//       历史记录、存读档（localStorage）、音频/视频宿主、HTML 关卡承载（iframe + postMessage）。
import type { StoryContent } from '../runtime/content.ts';
import type { LevelMessage, RuntimeEffect, RuntimeState } from '../runtime/kernel.ts';
import { applyRuntimeAction, restoreRuntime, saveRuntime, startRuntime } from '../runtime/kernel.ts';
import { validateStory } from '../runtime/content.ts';
import { LEVEL_PROTOCOL_VERSION, RUNTIME_KERNEL_VERSION } from '../runtime/version.ts';
import { prepareIsolatedLevel } from './level-frame.ts';
import './player.css';

interface ContentBundle {
  apiVersion: string;
  kernelVersion: number;
  projectId: string;
  revision: string;
  project: StoryContent['project'];
  chapters: StoryContent['chapters'];
  scenes: StoryContent['scenes'];
  variables: StoryContent['variables'];
  assets: StoryContent['assets'];
  characters: StoryContent['characters'];
  levels: StoryContent['levels'];
  theme?: StoryContent['theme'];
}

const SAVE_KEY = 'shiguang-player-save-v1';
/** 每个作品独立拥有三个存档槽，避免同源作品互相覆盖。 */
const SLOT_COUNT = 3;
const SETTINGS_KEY = 'shiguang-player-settings-v1';
const STATUS_LABELS: Record<RuntimeState['status'], string> = {
  running: '播放中', awaitingAdvance: '等待继续', awaitingChoice: '等待选择',
  awaitingEffect: '播放媒体或交互关卡', awaitingWait: '等待中', ended: '已结束', error: '播放异常',
};
const TEXT_SPEED_DELAY_MS: Record<TextSpeed, number> = { slow: 2200, normal: 1200, fast: 500 };
type TextSpeed = 'slow' | 'normal' | 'fast';
interface PlayerSettings { textSpeed: TextSpeed }
let settings: PlayerSettings = { textSpeed: 'normal' };
let activeSlot = 1;

function slotKey(slot: number): string {
  const project = encodeURIComponent(content?.project.projectId ?? bundle?.projectId ?? 'unloaded');
  return `${SAVE_KEY}:${project}${slot <= 1 ? '' : `:slot${slot}`}`;
}

function loadSettings(): void {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw) as Partial<PlayerSettings>;
    if (parsed.textSpeed === 'slow' || parsed.textSpeed === 'normal' || parsed.textSpeed === 'fast') settings = { textSpeed: parsed.textSpeed };
  } catch { /* 设置损坏时用默认值 */ }
}

function saveSettings(): boolean {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); return true; }
  catch { return false; }
}

/** 自动播放的等待时间由文本速度决定。 */
function autoDelayMs(): number {
  return TEXT_SPEED_DELAY_MS[settings.textSpeed];
}

const root = document.getElementById('player-root') as HTMLElement;
// 素材与关卡入口都相对于内容包目录解析（content.json 所在目录），保证发布包可以整体搬移。
let contentRoot = new URL('./content/', window.location.href);

let content: StoryContent | null = null;
let bundle: ContentBundle | null = null;
let state: RuntimeState | null = null;
let autoPlay = false;
let pendingTimer: number | null = null;
let pendingAutoTimer: number | null = null;
let waitUpdatedAt: number | null = null;
let waitEvent: string | null = null;
let mediaGeneration = 0;
let levelReadyTimer: number | null = null;
let levelController: AbortController | null = null;
let levelCleanup: (() => void) | null = null;
const fadeTimers = new Set<number>();
let levelFrame: HTMLIFrameElement | null = null;
let levelMessageSeq = 0;
/** 同一个关卡效果只承载一次：render() 会在等待期间被重复调用，不能重复重建 iframe。 */
let hostedLevelEffectId: string | null = null;
let levelMessageListener: ((event: MessageEvent) => void) | null = null;
/** 舞台节点在播放期间保持连接：只更新剧情和控件，不能移动已加载的 iframe。 */
const playerWrap = el('div', 'player');
const stage = el('div', 'stage');
stage.dataset.testid = 'stage';
const stageContent = el('div', 'stage-story');
const playerControls = el('div', 'player-controls');
const levelContainer = el('div', 'level-host');
levelContainer.id = 'level-host';
levelContainer.dataset.testid = 'level-host';
levelContainer.hidden = true;
stage.append(stageContent, levelContainer);
playerWrap.append(stage, playerControls);

/** 音频/视频宿主状态：同一个效果只承载一次（render 期间会被重复调用）。 */
let hostedAudioEffectId: string | null = null;
let hostedVideoEffectId: string | null = null;
let videoFrame: HTMLElement | null = null;
let activeSefx: { effectId: string; element: HTMLAudioElement } | null = null;
/** 已经交给宿主处理过的效果 ID：内核的 effects 列表是累积的，不能重复播放。 */
const handledEffectIds = new Set<string>();
/** 每条声道最多保留一个背景音实例，便于同声道替换。 */
const backgroundAudio = new Map<string, { element: HTMLAudioElement; assetId: string }>();
interface SavedHostAudio { assetId: string; channel: string; loop: boolean; volume: number }
type HostSaveRecord = Parameters<typeof restoreRuntime>[1] & { hostAudio?: SavedHostAudio[] };
const audioLayer = el('div', 'audio-layer');
audioLayer.id = 'audio-layer';
document.body.append(audioLayer);
/** 视频层同样放在顶层：render() 会重建剧情主体，视频不能被重建打断。 */
const videoLayer = el('div', 'video-layer');
videoLayer.id = 'video-layer';
document.body.append(videoLayer);

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className?: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function assetUrl(assetId: string | null | undefined): string | null {
  if (!assetId || !bundle) return null;
  const asset = bundle.assets.find((item) => item.assetId === assetId);
  if (!asset) return null;
  return new URL(asset.path, contentRoot).href;
}

let statusMessage = '';
let contentMissing = false;
function setStatus(text: string): void {
  // 记住状态文本：render() 会重建工具栏，消息必须能在重建后保留（否则保存提示会立刻消失）。
  statusMessage = text;
  const node = document.getElementById('player-status');
  if (node) node.textContent = text;
}

async function loadContent(url: string): Promise<void> {
  const response = await fetch(url, { cache: 'no-store' });
  contentMissing = response.status === 404;
  if (!response.ok) throw new Error(`无法加载内容包（HTTP ${response.status}）`);
  const loaded = await response.json() as ContentBundle;
  if (!loaded || loaded.kernelVersion !== RUNTIME_KERNEL_VERSION) throw new Error('作品内核版本不兼容，请重新导出作品');
  if (!loaded.project || loaded.projectId !== loaded.project.projectId || !['chapters', 'scenes', 'variables', 'assets', 'characters', 'levels'].every((key) => Array.isArray(loaded[key as keyof ContentBundle]))) throw new Error('作品内容包格式不完整');
  const candidate: StoryContent = {
    project: loaded.project,
    chapters: loaded.chapters,
    scenes: loaded.scenes,
    variables: loaded.variables,
    assets: loaded.assets,
    characters: loaded.characters,
    levels: loaded.levels,
    theme: loaded.theme,
  };
  const issues = validateStory(candidate);
  if (issues.length) throw new Error(`作品校验失败：${issues[0].message}`);
  bundle = loaded;
  content = candidate;
}

function disposeAudio(element: HTMLAudioElement): void {
  element.onended = null;
  element.onerror = null;
  element.pause();
  element.removeAttribute('src');
  element.load();
  element.remove();
}

function clearPlayback(): void {
  mediaGeneration += 1;
  if (pendingTimer !== null) window.clearTimeout(pendingTimer);
  if (pendingAutoTimer !== null) window.clearTimeout(pendingAutoTimer);
  pendingTimer = null;
  pendingAutoTimer = null;
  waitUpdatedAt = null;
  waitEvent = null;
  for (const timer of fadeTimers) window.clearInterval(timer);
  fadeTimers.clear();
  for (const element of audioLayer.querySelectorAll('audio')) disposeAudio(element);
  backgroundAudio.clear();
  activeSefx = null;
  hostedAudioEffectId = null;
  hostedVideoEffectId = null;
  handledEffectIds.clear();
  for (const element of videoLayer.querySelectorAll('video')) { element.onended = null; element.onerror = null; element.pause(); element.removeAttribute('src'); element.load(); }
  videoLayer.replaceChildren();
  videoFrame = null;
  clearHostedLevel();
}

function updateWaitElapsed(): void {
  if (!state || !content || state.status !== 'awaitingWait') { waitUpdatedAt = null; waitEvent = null; return; }
  const event = `${state.sceneId}:${state.eventIndex}`;
  const now = performance.now();
  if (waitEvent === event && waitUpdatedAt !== null) {
    const elapsed = Math.floor(now - waitUpdatedAt);
    if (elapsed > 0) state = applyRuntimeAction(content, state, { type: 'tick', elapsedMs: elapsed });
  }
  waitEvent = state.status === 'awaitingWait' ? `${state.sceneId}:${state.eventIndex}` : null;
  waitUpdatedAt = state.status === 'awaitingWait' ? now : null;
}

function titleScreen(error?: string, waitingForContent = false): void {
  clearPlayback();
  state = null;
  autoPlay = false;
  root.innerHTML = '';
  const wrap = el('div', 'title-screen');
  wrap.append(el('h1', undefined, bundle?.project.title ?? '拾光 Studio 展示器'));
  wrap.append(el('p', 'muted', bundle ? `内容修订 ${bundle.revision}｜内核 ${bundle.kernelVersion}｜场景 ${bundle.scenes.length} 个` : waitingForContent ? '尚未载入作品' : '正在加载内容包…'));
  if (waitingForContent) {
    const instructions = el('p', 'muted', '在编辑器中提交修改并点击“导出作品”，把导出目录中的整个 content 文件夹复制到展示器解压目录，再刷新此页面或重新启动展示器。');
    instructions.dataset.testid = 'empty-player';
    wrap.append(instructions);
  }
  if (error) wrap.append(el('p', 'error', error));
  const buttons = el('div', 'row');
  const start = el('button', undefined, '开始新游戏');
  start.dataset.testid = 'start';
  start.disabled = !content;
  start.onclick = () => {
    if (!content) return;
    state = startRuntime(content, content.project.entrySceneId, { revision: bundle?.revision ?? '' });
    render();
  };
  buttons.append(start);
  let saved: string | null = null;
  try { saved = localStorage.getItem(slotKey(activeSlot)); }
  catch { wrap.append(el('p', 'error', '浏览器本地存储不可用，当前无法保存或读取进度。')); }
  const continueButton = el('button', undefined, '继续上次进度');
  continueButton.dataset.testid = 'continue';
  continueButton.disabled = !content || !saved;
  continueButton.onclick = () => loadSave();
  buttons.append(continueButton);
  wrap.append(buttons);
  root.append(wrap);
}

function slotSummary(slot: number): string {
  try {
    const raw = localStorage.getItem(slotKey(slot));
    if (!raw) return '空';
    const record = JSON.parse(raw) as { savedAt?: string; revision?: string; status?: string };
    return `${record.savedAt ?? '—'}｜修订 ${record.revision ?? '—'}｜${record.status ?? ''}`;
  } catch {
    return '无法读取';
  }
}

function reportLoadFailure(message: string): void {
  if (state) { setStatus(message); render(); }
  else titleScreen(message);
}

function loadSave(slot = activeSlot): void {
  if (!content) return;
  try {
    const raw = localStorage.getItem(slotKey(slot));
    if (!raw) { reportLoadFailure(`槽位 ${slot} 没有找到存档，当前进度已保留。`); return; }
    const record = JSON.parse(raw) as HostSaveRecord;
    const restored = restoreRuntime(content, record, bundle?.revision ?? '');
    if (!restored.ok || !restored.state) {
      reportLoadFailure(`读档失败：${restored.reason ?? '未知原因'}（当前进度已保留）`);
      return;
    }
    // 读取成功后创建新实例，将存档快照重新交给关卡；普通 render 则保持原实例。
    clearPlayback();
    state = restored.state;
    if (Array.isArray(record.hostAudio)) for (const audio of record.hostAudio) {
      if (!audio || typeof audio !== 'object') continue;
      if (!['bgm', 'ambience'].includes(audio.channel) || !content.assets.some((asset) => asset.assetId === audio.assetId && asset.kind === 'audio')) continue;
      playAudio({ effectId: `restored-audio-${mediaGeneration}-${audio.channel}`, kind: 'audio', command: 'play', payload: { ...audio } }, false);
    }
    render();
  } catch (error) {
    reportLoadFailure(`无法读取存档：${(error as Error).message}（当前进度已保留）`);
  }
}

function saveGame(slot = activeSlot): void {
  if (!state) return;
  updateWaitElapsed();
  const record: HostSaveRecord = saveRuntime(state, new Date().toISOString());
  record.hostAudio = Array.from(backgroundAudio, ([channel, { assetId, element }]) => ({ channel, assetId, loop: element.loop, volume: element.volume }));
  try { localStorage.setItem(slotKey(slot), JSON.stringify(record)); setStatus(`已保存到槽位 ${slot}（当前作品的浏览器本地进度）`); }
  catch { setStatus('存档写入失败：浏览器存储空间不足或不可用，请释放空间后重试'); }
  render();
}

function clearSave(slot = activeSlot): void {
  try { localStorage.removeItem(slotKey(slot)); setStatus(`已删除槽位 ${slot} 的本地存档`); }
  catch { setStatus('无法删除存档：浏览器本地存储不可用'); }
  render();
}

function render(): void {
  if (!state || !content) return;
  updateWaitElapsed();
  if (pendingTimer !== null) {
    window.clearTimeout(pendingTimer);
    pendingTimer = null;
  }
  if (pendingAutoTimer !== null) { window.clearTimeout(pendingAutoTimer); pendingAutoTimer = null; }
  if (playerWrap.parentElement !== root) root.replaceChildren(playerWrap);
  stageContent.replaceChildren();
  playerControls.replaceChildren();
  const wrap = playerControls;
  const inLevel = state.pendingEffect?.kind === 'level';
  stage.classList.toggle('stage--level', inLevel);
  stageContent.hidden = inLevel;
  levelContainer.hidden = !inLevel;
  if (!inLevel && levelFrame) clearHostedLevel();

  const background = el('div', 'background');
  const bgUrl = assetUrl(state.stage.background);
  if (bgUrl) {
    const img = el('img');
    img.src = bgUrl;
    img.alt = state.stage.background ?? '';
    img.dataset.testid = 'background';
    img.onerror = () => { background.append(el('p', 'error', `背景素材无法加载：${state?.stage.background ?? ''}`)); };
    background.append(img);
  } else {
    background.append(el('p', 'muted', state.stage.background ? `背景素材缺失：${state.stage.background}` : '（未设置背景）'));
  }
  stageContent.append(background);

  for (const character of state.stage.characters) {
    const url = assetUrl(character.assetId);
    const layer = el('div', `character ${character.position}`);
    layer.dataset.testid = `character-${character.assetId}`;
    if (url) {
      const img = el('img');
      img.src = url;
      img.alt = character.assetId;
      img.onerror = () => { layer.append(el('p', 'error', `立绘素材无法加载：${character.assetId}`)); };
      layer.append(img);
    } else {
      layer.append(el('p', 'muted', character.assetId));
    }
    stageContent.append(layer);
  }

  const line = state.lines[state.lines.length - 1];
  const dialogue = el('div', 'dialogue');
  dialogue.dataset.testid = 'dialogue';
  // 主题（themes/theme.json）：展示器与编辑器/工具共用同一份数据
  const theme = bundle?.theme;
  if (theme) {
    dialogue.style.color = theme.dialogue.textColor;
    dialogue.style.fontSize = `${theme.dialogue.fontSize}px`;
    dialogue.style.textAlign = theme.dialogue.align;
    stage.style.setProperty('--accent', theme.menu.accentColor);
  }
  dialogue.append(el('span', 'speaker', line?.speaker || '旁白'));
  dialogue.append(el('span', 'text', line?.text ?? '（没有对白）'));
  stageContent.append(dialogue);

  if (state.pendingChoice) {
    const list = el('div', 'choices');
    list.dataset.testid = 'choices';
    if (state.pendingChoice.prompt) list.append(el('p', 'choice-prompt', state.pendingChoice.prompt));
    state.pendingChoice.options.forEach((option, index) => {
      const button = el('button', undefined, option.label);
      button.dataset.testid = `choice-${index}`;
      button.onclick = () => {
        if (!content || !state) return;
        state = applyRuntimeAction(content, state, { type: 'choose', option: index });
        render();
      };
      list.append(button);
    });
    wrap.append(list);
  } else if (state.status === 'awaitingAdvance') {
    const advance = el('button', 'advance', '继续（空格）');
    advance.dataset.testid = 'advance';
    advance.onclick = () => {
      if (!content || !state) return;
      state = applyRuntimeAction(content, state, { type: 'advance' });
      render();
    };
    wrap.append(advance);
  }

  if (state.status === 'awaitingWait' && state.pendingWaitMs !== null) {
    const remaining = state.pendingWaitMs;
    const waitNote = el('p', 'muted', `等待 ${remaining} 毫秒…`);
    waitNote.dataset.testid = 'waiting';
    wrap.append(waitNote);
    pendingTimer = window.setTimeout(() => {
      if (!content || !state) return;
      updateWaitElapsed();
      render();
    }, Math.min(remaining, 4000));
  }

  if (state.errors.length) {
    const errors = el('div', 'errors');
    errors.dataset.testid = 'errors';
    for (const error of state.errors) errors.append(el('p', 'error', `${error.code}：${error.message}`));
    if (state.status === 'error') {
      const retry = el('button', undefined, '返回标题页');
      retry.dataset.testid = 'restart';
      retry.onclick = () => titleScreen();
      errors.append(retry);
    }
    wrap.append(errors);
  }
  if (state.status === 'ended') {
    const ending = el('div', 'ending');
    ending.dataset.testid = 'ending';
    ending.append(el('h2', undefined, state.ended?.title ?? '结束'));
    const actions = el('div', 'row');
    const restart = el('button', undefined, '重新开始');
    restart.dataset.testid = 'restart';
    restart.onclick = () => { state = null; titleScreen(); };
    actions.append(restart);
    ending.append(actions);
    wrap.append(ending);
  }

  const toolbar = el('div', 'toolbar');
  toolbar.dataset.testid = 'toolbar';
  toolbar.append(el('span', 'revision', `修订 ${state.revision}｜${STATUS_LABELS[state.status]}`));

  // 多存档槽：选择槽位后，保存/读取/删除都作用于该槽位
  const slotSelect = el('select', 'slot-select') as HTMLSelectElement;
  slotSelect.dataset.testid = 'slot-select';
  for (let slot = 1; slot <= SLOT_COUNT; slot += 1) {
    const option = el('option', undefined, `槽位 ${slot}：${slotSummary(slot)}`) as HTMLOptionElement;
    option.value = String(slot);
    slotSelect.append(option);
  }
  slotSelect.value = String(activeSlot);
  slotSelect.onchange = () => { activeSlot = Number(slotSelect.value) || 1; render(); };
  toolbar.append(slotSelect);

  const save = el('button', undefined, `保存到槽位 ${activeSlot}`);
  save.dataset.testid = 'save';
  save.onclick = () => saveGame();
  toolbar.append(save);
  const load = el('button', undefined, '读取');
  load.dataset.testid = 'load';
  load.onclick = () => loadSave();
  toolbar.append(load);
  const clear = el('button', undefined, '删除存档');
  clear.dataset.testid = 'clear-save';
  clear.onclick = () => clearSave();
  toolbar.append(clear);
  const auto = el('button', undefined, autoPlay ? '关闭自动' : '自动播放');
  auto.dataset.testid = 'auto';
  auto.onclick = () => { autoPlay = !autoPlay; render(); };
  toolbar.append(auto);

  // 设置：文本速度决定自动播放等待时间
  const speedSelect = el('select', 'speed-select') as HTMLSelectElement;
  speedSelect.dataset.testid = 'text-speed';
  for (const [value, label] of [['slow', '慢'], ['normal', '正常'], ['fast', '快']] as const) {
    const option = el('option', undefined, `文本速度：${label}`) as HTMLOptionElement;
    option.value = value;
    speedSelect.append(option);
  }
  speedSelect.value = settings.textSpeed;
  speedSelect.onchange = () => {
    settings = { textSpeed: speedSelect.value as TextSpeed };
    const persisted = saveSettings();
    setStatus(`文本速度已设为 ${speedSelect.value}（自动播放等待 ${autoDelayMs()}ms）${persisted ? '' : '；浏览器存储不可用，刷新后将恢复默认速度'}`);
    render();
  };
  toolbar.append(speedSelect);
  toolbar.append(el('span', 'muted', `自动等待 ${autoDelayMs()}ms`));

  const status = el('span', 'muted', statusMessage);
  status.id = 'player-status';
  toolbar.append(status);
  wrap.append(toolbar);

  const history = el('details', 'history');
  history.dataset.testid = 'history';
  history.append(el('summary', undefined, `历史对白（${state.lines.length}）`));
  const list = el('ol');
  for (const item of state.lines) list.append(el('li', undefined, `${item.speaker || '旁白'}：${item.text}`));
  history.append(list);
  wrap.append(history);

  // 宿主能力在 DOM 构建完成后处理；若处理过程触发了新的 render（音频/视频未实现路径），
  // 则本次渲染立即结束，不再覆盖新的 DOM。
  const beforeEffects = state;
  drainHostEffects(); // 背景音先于紧随其后的阻塞视频/关卡执行。
  if (state !== beforeEffects) return; // 同步宿主失败已推进并重新 render。
  if (state.pendingEffect && handleEffect(state.pendingEffect)) return;

  if (autoPlay && state.status === 'awaitingAdvance') {
    pendingAutoTimer = window.setTimeout(() => {
      if (!autoPlay || !content || !state || state.status !== 'awaitingAdvance') return;
      state = applyRuntimeAction(content, state, { type: 'advance' });
      render();
    }, autoDelayMs());
  }
}

/** 宿主能力：音频/视频/关卡。返回 true 表示已经触发了一次新的 render（调用方不要再操作 DOM）。 */
function handleEffect(effect: RuntimeEffect): boolean {
  if (!content || !state) return false;
  if (effect.kind === 'audio') {
    // 音频统一在 drainHostEffects 中处理（含不等待的背景音），这里只处理需要回执的等待型音效
    if (state.pendingEffect?.effectId !== effect.effectId) return false;
    if (hostedAudioEffectId === effect.effectId) return false;
    hostedAudioEffectId = effect.effectId;
    drainHostEffects();
    return true;
  }
  if (effect.kind === 'video') {
    if (hostedVideoEffectId === effect.effectId && videoFrame) return false;
    hostedVideoEffectId = effect.effectId;
    playVideo(effect);
    return true;
  }
  if (effect.kind === 'level') {
    if (hostedLevelEffectId === effect.effectId && levelFrame) return false;
    hostLevel(effect);
  }
  return false;
}

/** 把宿主结果交回内核：成功则继续，失败则记录原因并继续（绝不让运行永久等待）。 */
function completeEffect(effect: RuntimeEffect, error?: { code: string; message: string }, result?: Record<string, unknown>): void {
  if (!content || !state) return;
  if (state.pendingEffect?.effectId !== effect.effectId) return;
  state = error
    ? applyRuntimeAction(content, state, { type: 'effectFailed', error })
    : applyRuntimeAction(content, state, { type: 'effectComplete', payload: result ?? {} });
  render();
}

/**
 * 音频播放：
 *  - 各声道遵循循环、音量和播放/停止/淡出参数；背景声道替换前一条。
 *  - 内核要求等待时播放一遍并交回结果，其余音频随剧情继续播放。
 * 任何失败（素材缺失、浏览器拒绝自动播放、解码错误）都带明确错误码交回内核，运行不会永久等待。
 */
function playAudio(effect: RuntimeEffect, blocking: boolean): void {
  const generation = mediaGeneration;
  const assetId = String(effect.payload.assetId ?? '');
  const channel = String(effect.payload.channel ?? 'bgm');
  const channelElements = Array.from(audioLayer.querySelectorAll('audio')).filter((element) => element.dataset.channel === channel);
  if (effect.command === 'stop' || effect.command === 'fade') {
    backgroundAudio.delete(channel);
    if (effect.command === 'stop' || !channelElements.length) {
      for (const element of channelElements) disposeAudio(element);
      setStatus(`已停止${channel === 'bgm' ? '背景音乐' : channel === 'ambience' ? '环境音' : channel === 'voice' ? '语音' : '音效'}`);
      if (blocking) completeEffect(effect);
      return;
    }
    const began = performance.now();
    const volumes = channelElements.map((element) => element.volume);
    const timer = window.setInterval(() => {
      if (generation !== mediaGeneration) { window.clearInterval(timer); fadeTimers.delete(timer); return; }
      const progress = Math.min(1, (performance.now() - began) / 300);
      channelElements.forEach((element, index) => { element.volume = volumes[index] * (1 - progress); });
      if (progress < 1) return;
      window.clearInterval(timer); fadeTimers.delete(timer);
      for (const element of channelElements) disposeAudio(element);
      if (blocking) completeEffect(effect);
    }, 25);
    fadeTimers.add(timer);
    return;
  }
  const url = assetUrl(assetId);
  if (!url) {
    setStatus(`音频素材缺失：${assetId}（已跳过，运行继续）`);
    if (blocking) completeEffect(effect, { code: 'AUDIO_ASSET_MISSING', message: `音频素材 ${assetId} 不在内容包中` });
    return;
  }
  if (channel === 'bgm' || channel === 'ambience') {
    // Replacing a channel releases its previous decoder and network request.
    const previous = backgroundAudio.get(channel);
    if (previous) disposeAudio(previous.element);
    const element = new Audio(url);
    // Waiting for a looping sound can never complete; waiting playback is one pass.
    element.loop = effect.payload.loop !== false && !blocking;
    element.dataset.testid = `audio-${channel}`;
    element.dataset.channel = channel;
    const volume = Number(effect.payload.volume ?? 1);
    element.volume = Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : 1;
    backgroundAudio.set(channel, { element, assetId });
    let finished = false;
    const finish = (error?: { code: string; message: string }): void => {
      if (finished || generation !== mediaGeneration) return;
      finished = true;
      if (backgroundAudio.get(channel)?.element === element) backgroundAudio.delete(channel);
      element.dataset.state = error ? 'failed' : 'ended';
      if (error) setStatus(error.message);
      if (blocking) completeEffect(effect, error, { played: !error, channel });
    };
    element.onended = () => finish();
    element.onerror = () => finish({ code: 'AUDIO_PLAYBACK_FAILED', message: `音频 ${assetId} 播放失败` });
    void element.play().catch((error: unknown) => {
      element.dataset.state = 'blocked';
      finish({ code: 'AUDIO_PLAYBACK_BLOCKED', message: `音频 ${assetId} 无法播放（${(error as Error).name}），已继续` });
    });
    audioLayer.append(element);
    setStatus(`播放背景音：${assetId}（${channel}）`);
    return;
  }
  const element = new Audio(url);
  element.dataset.testid = 'audio-sefx';
  element.dataset.channel = channel;
  element.loop = effect.payload.loop === true && !blocking;
  const volume = Number(effect.payload.volume ?? 1);
  element.volume = Number.isFinite(volume) ? Math.min(1, Math.max(0, volume)) : 1;
  let finished = false;
  const finish = (error?: { code: string; message: string }): void => {
    if (finished || generation !== mediaGeneration) return;
    finished = true;
    if (activeSefx?.effectId === effect.effectId) activeSefx = null;
    // 播放结束后保留节点（隐藏并标记状态）：便于排查“到底播没播”，用户看不到多余元素。
    element.hidden = true;
    element.dataset.state = error ? 'failed' : 'ended';
    if (error) setStatus(error.message);
    if (blocking) completeEffect(effect, error, error ? undefined : { played: true, channel });
  };
  element.onended = () => finish();
  element.onerror = () => finish({ code: 'AUDIO_PLAYBACK_FAILED', message: `音频 ${assetId} 播放失败` });
  if (blocking) activeSefx = { effectId: effect.effectId, element };
  audioLayer.append(element);
  void element.play().catch((error: unknown) => finish({ code: 'AUDIO_PLAYBACK_BLOCKED', message: `音频 ${assetId} 无法播放：${(error as Error).name}` }));
  setStatus(`播放音效：${assetId}`);
}

/**
 * 处理内核产生的全部宿主效果：只有 `pendingEffect` 需要回执，
 * 其余（例如不等待的背景音）也必须真正播放，否则就是“静默丢事件”。
 */
function drainHostEffects(): void {
  if (!content || !state) return;
  for (const effect of state.effects) {
    if (handledEffectIds.has(effect.effectId)) continue;
    handledEffectIds.add(effect.effectId);
    if (effect.kind !== 'audio') continue; // 视频/关卡由 pendingEffect 路径处理
    playAudio(effect, state.pendingEffect?.effectId === effect.effectId);
  }
}

/** 视频播放：显示在舞台上方，等待播放结束或用户跳过；失败同样交回内核继续。 */
function playVideo(effect: RuntimeEffect): void {
  const generation = mediaGeneration;
  const assetId = String(effect.payload.assetId ?? '');
  const url = assetUrl(assetId);
  videoFrame?.remove();
  videoFrame = null;
  if (!url) {
    setStatus(`视频素材缺失：${assetId}（已跳过，运行继续）`);
    completeEffect(effect, { code: 'VIDEO_ASSET_MISSING', message: `视频素材 ${assetId} 不在内容包中` });
    return;
  }
  const element = document.createElement('video');
  element.dataset.testid = 'video';
  element.src = url;
  element.controls = true;
  element.autoplay = true;
  const box = el('div', 'video-box');
  const skip = el('button', undefined, '跳过视频');
  skip.dataset.testid = 'video-skip';
  let finished = false;
  const finish = (error?: { code: string; message: string }): void => {
    if (finished || generation !== mediaGeneration) return;
    finished = true;
    element.onended = null;
    element.onerror = null;
    element.pause();
    element.removeAttribute('src');
    element.load();
    box.remove();
    videoFrame = null;
    completeEffect(effect, error, error ? undefined : { played: true });
  };
  element.onended = () => finish();
  element.onerror = () => finish({ code: 'VIDEO_PLAYBACK_FAILED', message: `视频 ${assetId} 播放失败` });
  skip.onclick = () => finish();
  box.append(element);
  if (effect.payload.skippable !== false) box.append(skip);
  videoLayer.append(box);
  videoFrame = box;
  void element.play().catch(() => {
    // 浏览器可能拒绝自动播放：保留控件让用户手动播放或跳过，不阻塞
    if (generation === mediaGeneration && !finished) setStatus(`视频 ${assetId} 需要手动播放${effect.payload.skippable === false ? '' : '或跳过'}`);
  });
  setStatus(`播放视频：${assetId}`);
}

function levelUrl(levelId: string): string | null {
  const level = bundle?.levels.find((item) => item.levelId === levelId);
  if (!level) return null;
  return new URL(level.entry, contentRoot).href;
}

/** 只有离开关卡或显式读档才释放实例；保存、checkpoint 和工具栏更新不能重载它。 */
function clearHostedLevel(): void {
  levelController?.abort();
  levelController = null;
  levelCleanup?.();
  levelCleanup = null;
  if (levelReadyTimer !== null) window.clearTimeout(levelReadyTimer);
  levelReadyTimer = null;
  if (levelMessageListener) window.removeEventListener('message', levelMessageListener);
  levelMessageListener = null;
  levelContainer.replaceChildren();
  levelFrame = null;
  hostedLevelEffectId = null;
}

/** HTML 关卡采用隔离来源，只接收当前 iframe 的合法消息。 */
function hostLevel(effect: RuntimeEffect): void {
  if (!content || !state) return;
  const levelId = String(effect.payload.levelId ?? '');
  const url = levelUrl(levelId);
  clearHostedLevel();
  if (!url) {
    levelContainer.append(el('p', 'error', `关卡 ${levelId} 未在工程中登记入口`));

    hostedLevelEffectId = null;
    state = applyRuntimeAction(content, state, { type: 'effectFailed', error: { code: 'LEVEL_NOT_FOUND', message: `关卡 ${levelId} 未登记` } });
    render();
    return;
  }
  hostedLevelEffectId = effect.effectId;
  const instanceId = `lvl_${levelId}_${Date.now().toString(36)}`;
  levelMessageSeq = 0;
  const frame = document.createElement('iframe');
  frame.className = 'level-frame';
  frame.dataset.testid = 'level-frame';
  frame.title = bundle?.levels.find((item) => item.levelId === levelId)?.title ?? 'HTML 关卡';
  levelFrame = frame;
  const controller = new AbortController();
  levelController = controller;
  const status = el('p', 'level-status', `${frame.title}：${effect.payload.supportsSnapshot === false ? '不支持快照（读档将重新开始）' : '支持快照'}`);
  status.dataset.testid = 'level-status';
  levelContainer.append(status, frame);

  const listener = (event: MessageEvent): void => {
    if (event.origin !== 'null' || event.source !== frame.contentWindow) return;
    if (!content || !state || state.pendingEffect?.effectId !== effect.effectId) return;
    const data = event.data as { type?: string; payload?: unknown; requestId?: string; levelInstanceId?: string; protocolVersion?: number; messageSeq?: number } | null;
    if (!data || typeof data !== 'object') return;
    if (data.levelInstanceId && data.levelInstanceId !== instanceId) return;
    if (data.protocolVersion !== undefined && data.protocolVersion !== LEVEL_PROTOCOL_VERSION) {
      clearHostedLevel();
      completeEffect(effect, { code: 'LEVEL_PROTOCOL_MISMATCH', message: '关卡消息协议版本不兼容' });
      return;
    }
    try { if (JSON.stringify(data.payload ?? null).length > 1024 * 1024) return; } catch { return; }
    const kind = data.type === 'ready' ? 'ready'
      : data.type === 'checkpoint' ? 'checkpoint'
      : data.type === 'complete' ? 'complete'
      : data.type === 'error' ? 'error'
      : data.type === 'init' ? 'init' : null;
    if (!kind) return;
    levelMessageSeq += 1;
    const message: LevelMessage = {
      kind,
      protocolVersion: data.protocolVersion ?? LEVEL_PROTOCOL_VERSION,
      levelInstanceId: instanceId,
      requestId: data.requestId ?? `${instanceId}-${levelMessageSeq}`,
      // ready/init have no progress sequence in the legacy protocol. Synthesising 1
      // here would discard a level's first explicit checkpoint with sequence 1.
      messageSeq: typeof data.messageSeq === 'number' && Number.isInteger(data.messageSeq) && data.messageSeq > 0 ? data.messageSeq : kind === 'ready' || kind === 'init' ? undefined : levelMessageSeq,
      payload: data.payload,
      error: kind === 'error' ? (data.payload as { code?: string; message?: string }) : undefined,
    };
    if (!content || !state) return;
    state = applyRuntimeAction(content, state, { type: 'levelMessage', message });
    if (kind === 'ready') {
      if (levelReadyTimer !== null) window.clearTimeout(levelReadyTimer);
      levelReadyTimer = null;
      // 关卡中途存档恢复（KI-017）：把内核保存的快照随 init 一并交给关卡程序。
      // 只有同一个关卡的快照才会下发，避免把别的关卡进度串进来。
      const savedSnapshot = effect.payload.supportsSnapshot !== false && state.levels.levelId === levelId ? state.levels.snapshot ?? null : null;
      frame.contentWindow?.postMessage({
        type: 'init',
        levelInstanceId: instanceId,
        protocolVersion: LEVEL_PROTOCOL_VERSION,
        payload: effect.payload.initPayload ?? null,
        snapshot: savedSnapshot,
      }, '*');
      if (savedSnapshot !== null) setStatus(`关卡 ${levelId} 已从存档快照恢复`);
    }
    if (kind === 'complete') {
      // levelMessage already completes this effect. A second completion consumes the next host event.
      if (state.pendingEffect?.effectId !== effect.effectId) clearHostedLevel();
    } else if (kind === 'error') {
      if (state.pendingEffect?.effectId !== effect.effectId) clearHostedLevel();
    } else {
      // 关卡进度点已写入运行状态：保存存档时会一并保存（读档时通过 init.snapshot 交回关卡）
      if (kind === 'checkpoint') setStatus(`关卡 ${levelId} 已记录进度点（保存存档时会一并保存）`);
    }
    render();
  };
  levelMessageListener = listener;
  window.addEventListener('message', listener);
  levelReadyTimer = window.setTimeout(() => {
    if (levelFrame !== frame || state?.pendingEffect?.effectId !== effect.effectId) return;
    clearHostedLevel();
    completeEffect(effect, { code: 'LEVEL_READY_TIMEOUT', message: `关卡 ${frame.title} 未完成初始化，已继续剧情` });
  }, 10000);
  void prepareIsolatedLevel(frame, url, contentRoot, effect.payload.initPayload, controller.signal).then((cleanup) => {
    if (controller.signal.aborted || levelFrame !== frame) cleanup();
    else levelCleanup = cleanup;
  }).catch((error: unknown) => {
    if (controller.signal.aborted || levelFrame !== frame || state?.pendingEffect?.effectId !== effect.effectId) return;
    clearHostedLevel();
    completeEffect(effect, { code: 'LEVEL_LOAD_FAILED', message: `关卡程序无法加载：${error instanceof Error ? error.message : '未知错误'}，已继续剧情` });
  });
}

function keyboard(event: KeyboardEvent): void {
  if (!content || !state) return;
  if (event.target instanceof HTMLElement && event.target.closest('button, input, select, textarea, [contenteditable="true"]')) return;
  if (event.code === 'Space' || event.key === 'Enter') {
    if (state.status === 'awaitingAdvance') {
      event.preventDefault();
      state = applyRuntimeAction(content, state, { type: 'advance' });
      render();
    }
  }
}

async function boot(): Promise<void> {
  loadSettings();
  const params = new URLSearchParams(window.location.search);
  const contentUrl = params.get('content') ?? './content/content.json';
  try {
    const resolved = new URL(contentUrl, window.location.href);
    contentRoot = new URL('./', resolved);
    await loadContent(resolved.href);
  } catch (error) {
    if (contentMissing && !params.has('content')) {
      titleScreen(undefined, true);
      return;
    }
    titleScreen(`内容包加载失败：${(error as Error).message}（请用发布包中的 serve.mjs 启动，或检查 ?content= 参数）`);
    return;
  }
  titleScreen();
  window.addEventListener('keydown', keyboard);
}

void boot();
