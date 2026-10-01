// 剧情运行内核：编辑器试玩与独立展示器共用这一实现。
// 约束：不使用时间/随机等环境输入，状态可 JSON 序列化；宿主能力（音频/视频/关卡）以 effect 形式交给宿主执行。
import type { StoryContent } from './content.ts';
import { initialVariables, sceneById } from './content.ts';
import { RUNTIME_KERNEL_VERSION } from './version.ts';

export type RuntimeStatus =
  | 'running' | 'awaitingAdvance' | 'awaitingChoice' | 'awaitingEffect'
  | 'awaitingWait' | 'ended' | 'error';

export interface RuntimeStage {
  background: string | null;
  characters: { assetId: string; position: string }[];
}

export interface RuntimeLine {
  sceneId: string;
  eventId: string;
  speaker: string;
  text: string;
  characterId: string | null;
}

export interface RuntimeEffect {
  effectId: string;
  kind: 'audio' | 'video' | 'level';
  command: string;
  payload: Record<string, unknown>;
  /** 视频失败重播次数；最多重播一次。 */
  attempt?: number;
}

export interface RuntimePendingChoice {
  choiceId: string;
  prompt: string;
  options: { label: string; index: number; targetEventId?: string; targetSceneId?: string }[];
}

export interface RuntimeError { code: string; message: string; sceneId?: string; eventId?: string }

export interface LevelRuntimeState {
  levelId: string | null;
  levelInstanceId: string | null;
  snapshot: unknown;
  result: unknown;
  completedRequests: string[];
  /** 已处理的最大消息序号（同一关卡实例内单调递增），用于丢弃迟到消息 */
  lastMessageSeq: number;
  errors: RuntimeError[];
}

export interface RuntimeState {
  kernelVersion: number;
  projectId: string;
  revision: string;
  sceneId: string;
  eventIndex: number;
  status: RuntimeStatus;
  stage: RuntimeStage;
  variables: Record<string, unknown>;
  lines: RuntimeLine[];
  pendingChoice: RuntimePendingChoice | null;
  pendingEffect: RuntimeEffect | null;
  pendingWaitMs: number | null;
  effects: RuntimeEffect[];
  errors: RuntimeError[];
  levels: LevelRuntimeState;
  ended: { reason: 'ending-event' | 'scene-end'; endingId: string | null; title: string } | null;
  steps: number;
}

export interface LevelMessage {
  kind: 'ready' | 'init' | 'checkpoint' | 'complete' | 'error';
  protocolVersion?: number;
  levelInstanceId?: string;
  requestId?: string;
  /** 关卡实例内的单调消息序号；用于识别重复与迟到消息 */
  messageSeq?: number;
  payload?: unknown;
  error?: { code?: string; message?: string };
}

export type RuntimeAction =
  | { type: 'advance' }
  | { type: 'choose'; option: number }
  | { type: 'effectComplete'; payload?: unknown }
  | { type: 'effectFailed'; error?: { code?: string; message?: string } }
  | { type: 'levelMessage'; message: LevelMessage }
  | { type: 'tick'; elapsedMs: number };

export interface StartOptions {
  revision?: string;
  variables?: Record<string, unknown>;
  /** 后台预览可显式注入起步时正在等待的关卡；不会传给后续关卡。 */
  levelSnapshot?: unknown;
}

const MAX_STEPS = 10000;

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function pushError(state: RuntimeState, error: RuntimeError): RuntimeState {
  state.errors = [...state.errors, error];
  state.status = 'error';
  return state;
}

export function startRuntime(content: StoryContent, sceneId: string, options: StartOptions = {}): RuntimeState {
  const state: RuntimeState = {
    kernelVersion: RUNTIME_KERNEL_VERSION,
    projectId: content.project.projectId,
    revision: options.revision ?? '',
    sceneId,
    eventIndex: 0,
    status: 'running',
    stage: { background: null, characters: [] },
    variables: { ...initialVariables(content), ...(options.variables ?? {}) },
    lines: [],
    pendingChoice: null,
    pendingEffect: null,
    pendingWaitMs: null,
    effects: [],
    errors: [],
    levels: {
      levelId: null,
      levelInstanceId: null,
      snapshot: null,
      result: null,
      completedRequests: [],
      lastMessageSeq: 0,
      errors: [],
    },
    ended: null,
    steps: 0,
  };
  if (!sceneById(content, sceneId)) {
    return pushError(state, { code: 'SCENE_NOT_FOUND', message: `起始场景 ${sceneId} 不存在`, sceneId });
  }
  const started = run(content, state);
  if (options.levelSnapshot !== undefined && started.pendingEffect?.kind === 'level' && started.pendingEffect.payload.supportsSnapshot !== false) {
    started.levels.snapshot = clone(options.levelSnapshot);
    started.pendingEffect.payload.snapshot = clone(options.levelSnapshot);
  }
  return started;
}

export function applyRuntimeAction(content: StoryContent, current: RuntimeState, action: RuntimeAction): RuntimeState {
  const state = clone(current);
  if (state.projectId !== content.project.projectId) return pushError(state, { code: 'PROJECT_MISMATCH', message: '运行状态不属于当前作品' });
  switch (action.type) {
    case 'advance': {
      if (state.status !== 'awaitingAdvance') return state;
      state.eventIndex += 1;
      state.status = 'running';
      return run(content, state);
    }
    case 'choose': {
      if (state.status !== 'awaitingChoice' || !state.pendingChoice) return state;
      const option = state.pendingChoice.options[action.option];
      state.pendingChoice = null;
      state.status = 'running';
      if (!option) return pushError(state, { code: 'INVALID_CHOICE', message: `选项序号 ${action.option} 不存在`, sceneId: state.sceneId });
      if (!moveToTarget(content, state, option.targetSceneId, option.targetEventId)) return state;
      return run(content, state);
    }
    case 'effectComplete': {
      if (state.status !== 'awaitingEffect' || !state.pendingEffect) return state;
      const effect = state.pendingEffect;
      state.pendingEffect = null;
      state.status = 'running';
      if (effect.kind === 'level') {
        state.levels.result = action.payload ?? null;
      }
      if (effect.kind === 'video' && effect.payload.onEnd === 'stop') {
        state.ended = { reason: 'scene-end', endingId: null, title: '视频播放后结束' };
        state.status = 'ended';
        return state;
      }
      // 宿主能力完成后该事件即被消费，继续下一条事件。
      state.eventIndex += 1;
      return run(content, state);
    }
    case 'effectFailed': {
      if (state.status !== 'awaitingEffect' || !state.pendingEffect) return state;
      const effect = state.pendingEffect;
      state.pendingEffect = null;
      state.status = 'running';
      pushError(state, {
        code: action.error?.code ?? 'EFFECT_FAILED',
        message: action.error?.message ?? `${effect.kind} 宿主执行失败`,
        sceneId: state.sceneId,
      });
      // 媒体失败不能让运行永久等待：记录错误后按事件声明的策略继续。
      const scene = sceneById(content, state.sceneId);
      const event = scene?.events[state.eventIndex];
      const onError = String(event?.params.onError ?? 'continue');
      state.status = 'running';
      if (onError === 'stop') {
        state.ended = { reason: 'scene-end', endingId: null, title: '媒体失败后结束' };
        state.status = 'ended';
        return state;
      }
      if (effect.kind === 'video' && onError === 'replay' && (effect.attempt ?? 0) < 1) {
        state.pendingEffect = { ...effect, effectId: `${effect.effectId}_retry1`, attempt: 1 };
        state.status = 'awaitingEffect';
        return state;
      }
      state.eventIndex += 1;
      return run(content, state);
    }
    case 'levelMessage': {
      const message = action.message;
      // 离开关卡后不再接收它的消息，旧实例也不能覆盖新实例的结果。
      if (state.status !== 'awaitingEffect' || state.pendingEffect?.kind !== 'level') return state;
      if (message.kind !== 'ready' && message.levelInstanceId && state.levels.levelInstanceId && message.levelInstanceId !== state.levels.levelInstanceId) return state;
      if (message.kind === 'ready' && message.levelInstanceId && message.levelInstanceId !== state.levels.levelInstanceId) {
        // 消息序号属于关卡实例；跨关卡或读档后的新实例从头计数，快照仍保留。
        state.levels.lastMessageSeq = 0;
        state.levels.completedRequests = [];
      }
      const requestId = message.requestId ?? '';
      const seq = typeof message.messageSeq === 'number' ? message.messageSeq : null;
      const stale = (seq !== null && seq <= state.levels.lastMessageSeq) || (!!requestId && state.levels.completedRequests.includes(requestId));
      if (stale && (message.kind === 'checkpoint' || message.kind === 'complete' || message.kind === 'error')) {
        // 重复或迟到消息：只记录，不重复推进、不覆盖已保存的快照或结果。
        state.levels.lastMessageSeq = seq !== null ? Math.max(seq, state.levels.lastMessageSeq) : state.levels.lastMessageSeq;
        return state;
      }
      if (seq !== null) state.levels.lastMessageSeq = Math.max(seq, state.levels.lastMessageSeq);
      if (requestId && (message.kind === 'checkpoint' || message.kind === 'complete')) {
        state.levels.completedRequests = [...state.levels.completedRequests, requestId];
      }
      if (message.kind === 'checkpoint') {
        if (state.pendingEffect.payload.supportsSnapshot !== false) {
          state.levels.snapshot = message.payload ?? state.levels.snapshot;
        }
        state.levels.levelInstanceId = message.levelInstanceId ?? state.levels.levelInstanceId;
        return state;
      }
      if (message.kind === 'complete') {
        if (state.status === 'awaitingEffect' && state.pendingEffect?.kind === 'level') return applyRuntimeAction(content, state, { type: 'effectComplete', payload: message.payload ?? null });
        state.levels.result = message.payload ?? null;
        return state;
      }
      if (message.kind === 'error') {
        state.levels.levelInstanceId = message.levelInstanceId ?? state.levels.levelInstanceId;
        state.levels.errors = [...state.levels.errors, { code: message.error?.code ?? 'LEVEL_ERROR', message: message.error?.message ?? '关卡执行失败' }];
        if (state.status === 'awaitingEffect' && state.pendingEffect?.kind === 'level') {
          return applyRuntimeAction(content, state, { type: 'effectFailed', error: { code: message.error?.code ?? 'LEVEL_ERROR', message: message.error?.message ?? '关卡执行失败' } });
        }
        return state;
      }
      if (message.kind === 'ready' || message.kind === 'init') {
        state.levels.levelInstanceId = message.levelInstanceId ?? state.levels.levelInstanceId;
        return state;
      }
      return state;
    }
    case 'tick': {
      if (state.status !== 'awaitingWait' || state.pendingWaitMs === null) return state;
      const elapsed = Math.max(0, Number(action.elapsedMs) || 0);
      const remaining = state.pendingWaitMs - elapsed;
      if (remaining > 0) {
        state.pendingWaitMs = remaining;
        return state;
      }
      state.pendingWaitMs = null;
      state.status = 'running';
      state.eventIndex += 1;
      return run(content, state);
    }
    default:
      return state;
  }
}

function effectIdFor(state: RuntimeState, eventId: string, suffix: string): string {
  return `eff_${eventId}_${suffix}_${state.steps}`;
}

/** 事件 ID 在工程内唯一；同时指定场景时必须属于该场景。 */
function moveToTarget(content: StoryContent, state: RuntimeState, sceneId?: string, eventId?: string): boolean {
  const sourceSceneId = state.sceneId;
  const sourceEventId = sceneById(content, sourceSceneId)?.events[state.eventIndex]?.eventId;
  const targetScene = sceneId ? sceneById(content, sceneId) : eventId ? content.scenes.find((scene) => scene.events.some((event) => event.eventId === eventId)) : undefined;
  if (!targetScene) {
    pushError(state, { code: sceneId ? 'SCENE_NOT_FOUND' : 'EVENT_NOT_FOUND', message: sceneId ? `目标场景 ${sceneId} 不存在` : `目标事件 ${String(eventId)} 不存在`, sceneId: sourceSceneId, eventId: sourceEventId });
    return false;
  }
  const index = eventId ? targetScene.events.findIndex((event) => event.eventId === eventId) : 0;
  if (index < 0) {
    pushError(state, { code: 'EVENT_SCENE_MISMATCH', message: `目标事件 ${eventId} 不属于场景 ${targetScene.sceneId}`, sceneId: sourceSceneId, eventId: sourceEventId });
    return false;
  }
  state.sceneId = targetScene.sceneId;
  state.eventIndex = index;
  return true;
}

function run(content: StoryContent, state: RuntimeState): RuntimeState {
  let steps = 0;
  while (state.status === 'running') {
    steps += 1;
    state.steps += 1;
    if (steps > MAX_STEPS) return pushError(state, { code: 'RUNTIME_LOOP', message: '事件推进超过上限，可能存在循环跳转', sceneId: state.sceneId });
    const scene = sceneById(content, state.sceneId);
    if (!scene) return pushError(state, { code: 'SCENE_NOT_FOUND', message: `场景 ${state.sceneId} 不存在`, sceneId: state.sceneId });
    const event = scene.events[state.eventIndex];
    if (!event) {
      state.ended = { reason: 'scene-end', endingId: null, title: '场景结束' };
      state.status = 'ended';
      return state;
    }
    const params = event.params ?? {};
    switch (event.type) {
      case 'background': {
        state.stage = { ...state.stage, background: String(params.assetId ?? '') || null };
        state.eventIndex += 1;
        break;
      }
      case 'character': {
        const assetId = String(params.assetId ?? '');
        const position = String(params.position ?? 'center');
        const action = String(params.action ?? 'enter');
        const others = state.stage.characters.filter((item) => item.assetId !== assetId);
        if (action === 'exit') state.stage = { ...state.stage, characters: others };
        else state.stage = { ...state.stage, characters: [...others, { assetId, position }] };
        state.eventIndex += 1;
        break;
      }
      case 'dialogue': {
        state.lines = [...state.lines, {
          sceneId: state.sceneId,
          eventId: event.eventId,
          speaker: String(params.speaker ?? ''),
          text: String(params.text ?? ''),
          characterId: params.characterId ? String(params.characterId) : null,
        }];
        state.status = 'awaitingAdvance';
        return state;
      }
      case 'choice': {
        const options = Array.isArray(params.options) ? params.options as Record<string, unknown>[] : [];
        state.pendingChoice = {
          choiceId: `${state.sceneId}:${event.eventId}`,
          prompt: String(params.prompt ?? ''),
          options: options.map((option, index) => ({
            label: String(option.label ?? `选项${index + 1}`),
            index,
            targetEventId: option.targetEventId ? String(option.targetEventId) : undefined,
            targetSceneId: option.targetSceneId ? String(option.targetSceneId) : undefined,
          })),
        };
        state.status = 'awaitingChoice';
        return state;
      }
      case 'variable': {
        const variableId = String(params.variableId ?? '');
        const definition = content.variables.find((item) => item.variableId === variableId);
        if (!definition) return pushError(state, { code: 'VARIABLE_NOT_FOUND', message: `变量 ${variableId} 不存在`, sceneId: state.sceneId, eventId: event.eventId });
        const raw = params.value;
        const op = String(params.op ?? 'set');
        if (op === 'add') {
          const base = Number(state.variables[variableId] ?? definition.initial ?? 0);
          const delta = Number(raw);
          if (Number.isNaN(delta)) return pushError(state, { code: 'INVALID_VARIABLE_VALUE', message: `变量 ${variableId} 的增量不是数字`, sceneId: state.sceneId, eventId: event.eventId });
          state.variables = { ...state.variables, [variableId]: base + delta };
        } else {
          const value = definition.type === 'number' ? Number(raw) : definition.type === 'boolean' ? raw === true || raw === 'true' : String(raw);
          if (definition.type === 'number' && Number.isNaN(value as number)) return pushError(state, { code: 'INVALID_VARIABLE_VALUE', message: `变量 ${variableId} 需要数字`, sceneId: state.sceneId, eventId: event.eventId });
          state.variables = { ...state.variables, [variableId]: value };
        }
        state.eventIndex += 1;
        break;
      }
      case 'audio': {
        const effect: RuntimeEffect = {
          effectId: effectIdFor(state, event.eventId, 'audio'),
          kind: 'audio',
          command: String(params.action ?? 'play'),
          payload: {
            assetId: String(params.assetId ?? ''),
            channel: String(params.channel ?? 'bgm'),
            loop: params.loop !== false,
            volume: typeof params.volume === 'number' ? params.volume : 1,
          },
        };
        state.effects = [...state.effects, effect];
        if (params.waitForEnd === true) {
          state.pendingEffect = effect;
          state.status = 'awaitingEffect';
          return state;
        }
        state.eventIndex += 1;
        break;
      }
      case 'video': {
        const effect: RuntimeEffect = {
          effectId: effectIdFor(state, event.eventId, 'video'),
          kind: 'video',
          command: 'play',
          payload: {
            assetId: String(params.assetId ?? ''),
            skippable: params.skippable !== false,
            onEnd: String(params.onEnd ?? 'continue'),
          },
        };
        state.pendingEffect = effect;
        state.status = 'awaitingEffect';
        return state;
      }
      case 'wait': {
        const ms = Number(params.ms ?? 0);
        if (!Number.isFinite(ms) || ms < 0) return pushError(state, { code: 'INVALID_WAIT', message: `等待时间不合法：${String(params.ms)}`, sceneId: state.sceneId, eventId: event.eventId });
        if (ms === 0) { state.eventIndex += 1; break; }
        state.pendingWaitMs = ms;
        state.status = 'awaitingWait';
        return state;
      }
      case 'level': {
        const levelId = String(params.levelId ?? '');
        const definition = content.levels.find((level) => level.levelId === levelId);
        if (!definition) return pushError(state, { code: 'LEVEL_NOT_FOUND', message: `关卡 ${levelId} 不存在`, sceneId: state.sceneId, eventId: event.eventId });
        const supportsSnapshot = params.supportsSnapshot !== false && definition.supportsSnapshot !== false;
        // 每次进入（包括同关卡循环）都创建新运行；恢复只走 restoreRuntime。
        state.levels = { levelId, levelInstanceId: null, snapshot: null, result: null, completedRequests: [], lastMessageSeq: 0, errors: [] };
        const effect: RuntimeEffect = {
          effectId: effectIdFor(state, event.eventId, 'level'),
          kind: 'level',
          command: 'enter',
          payload: {
            levelId,
            supportsSnapshot,
            initPayload: params.initPayload ?? null,
            snapshot: null,
          },
        };
        state.pendingEffect = effect;
        state.status = 'awaitingEffect';
        return state;
      }
      case 'jump': {
        const targetScene = params.targetSceneId ? String(params.targetSceneId) : '';
        const targetEvent = params.targetEventId ? String(params.targetEventId) : '';
        if (targetScene || targetEvent) {
          if (!moveToTarget(content, state, targetScene, targetEvent)) return state;
        } else {
          state.eventIndex += 1;
        }
        break;
      }
      case 'ending': {
        state.ended = {
          reason: 'ending-event',
          endingId: params.endingId ? String(params.endingId) : null,
          title: String(params.title ?? params.endingId ?? '结束'),
        };
        state.status = 'ended';
        return state;
      }
      default:
        return pushError(state, { code: 'UNKNOWN_EVENT_TYPE', message: `运行内核不支持的事件类型 ${String(event.type)}`, sceneId: state.sceneId, eventId: event.eventId });
    }
    if (state.eventIndex >= 10000) return pushError(state, { code: 'RUNTIME_LOOP', message: '事件索引超出上限', sceneId: state.sceneId });
  }
  return state;
}

export interface RuntimeSaveRecord {
  kernelVersion: number;
  projectId: string;
  revision: string;
  sceneId: string;
  eventIndex: number;
  status: RuntimeStatus;
  variables: Record<string, unknown>;
  stage: RuntimeStage;
  lines: RuntimeLine[];
  levels: LevelRuntimeState;
  pendingChoice: RuntimePendingChoice | null;
  pendingEffect: RuntimeEffect | null;
  pendingWaitMs: number | null;
  ended: RuntimeState['ended'];
  errors: RuntimeError[];
  steps: number;
  savedAt: string;
}

export function saveRuntime(state: RuntimeState, savedAt: string): RuntimeSaveRecord {
  const record: RuntimeSaveRecord = clone({
    kernelVersion: state.kernelVersion,
    projectId: state.projectId,
    revision: state.revision,
    sceneId: state.sceneId,
    eventIndex: state.eventIndex,
    status: state.status,
    variables: state.variables,
    stage: state.stage,
    lines: state.lines,
    levels: state.levels,
    pendingChoice: state.pendingChoice,
    pendingEffect: state.pendingEffect,
    pendingWaitMs: state.pendingWaitMs,
    ended: state.ended,
    errors: state.errors,
    steps: state.steps,
    savedAt,
  });
  if (record.pendingEffect?.kind === 'level') {
    const snapshot = record.pendingEffect.payload.supportsSnapshot === false ? null : record.levels.snapshot;
    record.levels.snapshot = snapshot;
    record.pendingEffect.payload.snapshot = snapshot;
  }
  return record;
}

export interface RestoreResult { ok: boolean; reason?: string; state?: RuntimeState }

/** 读档：只接受与当前内容修订一致、且内核版本兼容的存档，避免旧回调重复推进。 */
export function restoreRuntime(content: StoryContent, record: RuntimeSaveRecord, currentRevision: string): RestoreResult {
  if (!record || typeof record !== 'object') return { ok: false, reason: '存档格式不正确' };
  if (typeof record.projectId !== 'string' || !record.projectId) return { ok: false, reason: '存档缺少作品 ID，无法确认所属作品，需重新开始' };
  if (record.projectId !== content.project.projectId) return { ok: false, reason: '存档属于其他作品，需重新开始' };
  if (record.kernelVersion !== RUNTIME_KERNEL_VERSION) return { ok: false, reason: `存档内核版本 ${String(record.kernelVersion)} 与当前 ${RUNTIME_KERNEL_VERSION} 不一致` };
  if (record.revision !== currentRevision) return { ok: false, reason: `存档修订 ${String(record.revision)} 与当前内容修订 ${currentRevision} 不一致，需重新开始` };
  const scene = sceneById(content, record.sceneId);
  if (!scene) return { ok: false, reason: `存档场景 ${String(record.sceneId)} 已不存在` };
  const malformed = validateSaveRecord(record, scene.events.length);
  if (malformed) return { ok: false, reason: `存档损坏：${malformed}` };
  const event = scene.events[record.eventIndex];
  const eventType = event?.type;
  if ((record.status === 'awaitingAdvance' && eventType !== 'dialogue')
    || (record.status === 'awaitingChoice' && eventType !== 'choice')
    || (record.status === 'awaitingWait' && eventType !== 'wait')
    || (record.status === 'awaitingEffect' && eventType !== record.pendingEffect?.kind)
    || (record.status !== 'ended' && record.status !== 'error' && !event)) {
    return { ok: false, reason: '存档损坏：停留状态与事件不一致' };
  }
  if (record.status === 'awaitingAdvance') {
    const line = record.lines.at(-1);
    if (line?.sceneId !== scene.sceneId || line.eventId !== event?.eventId) return { ok: false, reason: '存档损坏：当前对白缺失' };
  }
  if (record.status === 'awaitingChoice' && record.pendingChoice?.choiceId !== `${scene.sceneId}:${event?.eventId}`) return { ok: false, reason: '存档损坏：选项不属于当前事件' };
  if (record.status === 'awaitingWait' && record.pendingWaitMs! > Number(event?.params.ms)) return { ok: false, reason: '存档损坏：剩余等待时间超出事件时长' };
  if (record.status === 'awaitingEffect' && eventType === 'audio' && event?.params.waitForEnd !== true) return { ok: false, reason: '存档损坏：音频事件不等待完成' };
  const state: RuntimeState = clone({
    kernelVersion: RUNTIME_KERNEL_VERSION,
    projectId: content.project.projectId,
    revision: currentRevision,
    sceneId: record.sceneId,
    eventIndex: record.eventIndex,
    status: record.status,
    stage: record.stage,
    variables: record.variables,
    lines: record.lines,
    pendingChoice: record.pendingChoice,
    pendingEffect: record.pendingEffect,
    pendingWaitMs: record.pendingWaitMs,
    effects: record.pendingEffect?.kind === 'audio' ? [record.pendingEffect] : [],
    errors: record.errors,
    levels: record.levels,
    ended: record.ended,
    steps: record.steps,
  });
  if (state.pendingEffect?.kind === 'level') {
    const levelId = String(event?.params.levelId ?? '');
    const level = content.levels.find((item) => item.levelId === levelId);
    if (!level || state.levels.levelId !== levelId || state.pendingEffect.payload.levelId !== levelId) return { ok: false, reason: '存档损坏：待恢复关卡与当前事件不一致' };
    const supportsSnapshot = event?.params.supportsSnapshot !== false && level.supportsSnapshot !== false;
    state.pendingEffect.payload.supportsSnapshot = supportsSnapshot;
    if (!supportsSnapshot) state.levels.snapshot = null;
    state.pendingEffect.payload.snapshot = state.levels.snapshot;
  }
  // 对白/选择/等待/结局直接保留停留状态，避免重跑当前事件造成重复历史或重置等待。
  return { ok: true, state: state.status === 'running' ? run(content, state) : state };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}

function validErrors(value: unknown): value is RuntimeError[] {
  return Array.isArray(value) && value.every((item) => isObject(item) && typeof item.code === 'string' && typeof item.message === 'string');
}

function validEffect(value: unknown): value is RuntimeEffect {
  return isObject(value) && typeof value.effectId === 'string' && value.effectId.length > 0
    && ['audio', 'video', 'level'].includes(String(value.kind)) && typeof value.command === 'string' && isObject(value.payload)
    && (value.attempt === undefined || value.attempt === 0 || value.attempt === 1);
}

/** 所有存档都当作外部输入；拒绝损坏字段后才深拷贝并交给宿主。 */
function validateSaveRecord(record: RuntimeSaveRecord, eventCount: number): string | null {
  if (!Number.isInteger(record.eventIndex) || record.eventIndex < 0 || record.eventIndex > eventCount) return '事件位置不合法';
  if (!['running', 'awaitingAdvance', 'awaitingChoice', 'awaitingEffect', 'awaitingWait', 'ended', 'error'].includes(record.status)) return '运行状态不合法';
  if (!isObject(record.variables)) return '变量格式不正确';
  if (!isObject(record.stage) || !(record.stage.background === null || typeof record.stage.background === 'string') || !Array.isArray(record.stage.characters)
    || !record.stage.characters.every((item) => isObject(item) && typeof item.assetId === 'string' && typeof item.position === 'string')) return '舞台格式不正确';
  if (!Array.isArray(record.lines) || !record.lines.every((item) => isObject(item) && typeof item.sceneId === 'string' && typeof item.eventId === 'string' && typeof item.speaker === 'string' && typeof item.text === 'string' && (item.characterId === null || typeof item.characterId === 'string'))) return '对白历史格式不正确';
  const levels = record.levels;
  if (!isObject(levels) || !(levels.levelId === null || typeof levels.levelId === 'string') || !(levels.levelInstanceId === null || typeof levels.levelInstanceId === 'string')
    || !Number.isInteger(levels.lastMessageSeq) || levels.lastMessageSeq < 0 || !Array.isArray(levels.completedRequests) || !levels.completedRequests.every((id) => typeof id === 'string') || !validErrors(levels.errors)) return '关卡状态格式不正确';
  const choice = record.pendingChoice;
  if (choice !== null && (!isObject(choice) || typeof choice.choiceId !== 'string' || typeof choice.prompt !== 'string' || !Array.isArray(choice.options) || choice.options.length < 1 || choice.options.length > 6
    || !choice.options.every((option, index) => isObject(option) && typeof option.label === 'string' && option.index === index
      && (option.targetSceneId === undefined || typeof option.targetSceneId === 'string') && (option.targetEventId === undefined || typeof option.targetEventId === 'string')))) return '选择格式不正确';
  if (record.pendingEffect !== null && !validEffect(record.pendingEffect)) return '宿主效果格式不正确';
  if (record.pendingWaitMs !== null && (!Number.isFinite(record.pendingWaitMs) || record.pendingWaitMs <= 0)) return '剩余等待时间不合法';
  const ended = record.ended;
  if (ended !== null && (!isObject(ended) || !['ending-event', 'scene-end'].includes(ended.reason) || !(ended.endingId === null || typeof ended.endingId === 'string') || typeof ended.title !== 'string')) return '结局格式不正确';
  if (!validErrors(record.errors) || !Number.isInteger(record.steps) || record.steps < 0 || typeof record.savedAt !== 'string') return '存档元信息格式不正确';
  if ((record.status === 'awaitingChoice') !== (choice !== null) || (record.status === 'awaitingEffect') !== (record.pendingEffect !== null)
    || (record.status === 'awaitingWait') !== (record.pendingWaitMs !== null) || (record.status === 'ended') !== (ended !== null)) return '停留状态缺少对应信息';
  try { JSON.stringify(record); } catch { return '存档包含无法保存的数据'; }
  return null;
}
