// 事件目录：后台校验、运行内核、编辑器表单和 AI 工具共用同一份参数规范。
// 只登记当前运行内核真正执行的能力；runtimeSupport 为 planned 的事件不会在编辑器里显示为可用。
import { EVENT_CATALOG_VERSION } from './version.ts';

export type AssetKind = 'background' | 'character' | 'cg' | 'audio' | 'video';
export type EventType =
  | 'background' | 'character' | 'dialogue' | 'choice' | 'variable'
  | 'audio' | 'video' | 'wait' | 'level' | 'jump' | 'ending';

export type ParamValueType =
  | 'text' | 'longText' | 'number' | 'boolean' | 'assetRef' | 'sceneRef'
  | 'eventRef' | 'variableRef' | 'levelRef' | 'enum' | 'list';

export interface ParamSpec {
  key: string;
  label: string;
  type: ParamValueType;
  required?: boolean;
  enumValues?: string[];
  assetKinds?: AssetKind[];
  min?: number;
  max?: number;
  maxLength?: number;
  default?: unknown;
  itemFields?: ParamSpec[];
  optional?: boolean;
}

export type RuntimeSupport = 'implemented' | 'host_required';

export interface EventSpec {
  type: EventType;
  label: string;
  since: number;
  runtimeSupport: RuntimeSupport;
  hostCapability?: 'audio' | 'video' | 'level';
  waits?: boolean;
  params: ParamSpec[];
  summary: (params: Record<string, unknown>) => string;
}

export interface EventValidationContext {
  /** assetId -> 素材类型 */
  assets?: Record<string, AssetKind>;
  sceneIds?: string[];
  /** sceneId -> 该场景事件 ID 列表 */
  eventIds?: Record<string, string[]>;
  variableIds?: string[];
  levelIds?: string[];
  characterIds?: string[];
}

export interface ParamIssue { code: string; field?: string; message: string }

const text = (key: string, label: string, extra: Partial<ParamSpec> = {}): ParamSpec => ({ key, label, type: 'text', ...extra });
const str = (v: unknown): string => (typeof v === 'string' ? v : '');

function requireText(params: Record<string, unknown>, key: string, label: string, issues: ParamIssue[], maxLength = 2000): void {
  const value = params[key];
  if (typeof value !== 'string' || !value.trim()) issues.push({ code: 'REQUIRED', field: key, message: `${label}不能为空` });
  else if (value.length > maxLength) issues.push({ code: 'TOO_LONG', field: key, message: `${label}最长 ${maxLength} 字` });
}

export const EVENT_SPECS: Record<EventType, EventSpec> = {
  background: {
    type: 'background', label: '背景', since: 1, runtimeSupport: 'implemented',
    summary: (p) => `背景 → ${str(p.assetId)}`,
    params: [{ key: 'assetId', label: '背景素材', type: 'assetRef', required: true, assetKinds: ['background', 'cg'] }],
  },
  character: {
    type: 'character', label: '人物演出', since: 1, runtimeSupport: 'implemented',
    summary: (p) => `人物 ${str(p.assetId)} ${str(p.action) === 'exit' ? '退场' : str(p.action) === 'move' ? '移动' : '入场'}（${str(p.position)}）`,
    params: [
      { key: 'assetId', label: '人物立绘', type: 'assetRef', required: true, assetKinds: ['character'] },
      { key: 'action', label: '动作', type: 'enum', required: true, enumValues: ['enter', 'exit', 'move'], default: 'enter' },
      { key: 'position', label: '位置', type: 'enum', required: true, enumValues: ['left', 'center', 'right'], default: 'center' },
      { key: 'durationMs', label: '时长(毫秒)', type: 'number', min: 0, max: 10000, default: 300, optional: true },
    ],
  },
  dialogue: {
    type: 'dialogue', label: '对白', since: 1, runtimeSupport: 'implemented', waits: true,
    summary: (p) => `${str(p.speaker) || '旁白'}：${str(p.text).slice(0, 40)}`,
    params: [
      text('speaker', '说话人', { maxLength: 40, optional: true }),
      { key: 'text', label: '台词', type: 'longText', required: true, maxLength: 1200 },
      { key: 'characterId', label: '说话角色立绘', type: 'assetRef', assetKinds: ['character'], optional: true },
    ],
  },
  choice: {
    type: 'choice', label: '选择', since: 1, runtimeSupport: 'implemented', waits: true,
    summary: (p) => `选择（${Array.isArray(p.options) ? p.options.length : 0} 项）`,
    params: [
      text('prompt', '提示', { maxLength: 200, optional: true }),
      {
        key: 'options', label: '选项', type: 'list', required: true, itemFields: [
          text('label', '选项文字', { required: true, maxLength: 120 }),
          text('targetEventId', '目标事件 ID', { optional: true }),
          text('targetSceneId', '目标场景 ID', { optional: true }),
        ],
      },
    ],
  },
  variable: {
    type: 'variable', label: '变量更新', since: 1, runtimeSupport: 'implemented',
    summary: (p) => `变量 ${str(p.variableId)} ${str(p.op) === 'add' ? '+=' : '='} ${JSON.stringify(p.value ?? null)}`,
    params: [
      { key: 'variableId', label: '变量', type: 'variableRef', required: true },
      { key: 'op', label: '操作', type: 'enum', required: true, enumValues: ['set', 'add'], default: 'set' },
      { key: 'value', label: '值', type: 'text', required: true, maxLength: 200 },
    ],
  },
  audio: {
    type: 'audio', label: '音频', since: 1, runtimeSupport: 'host_required', hostCapability: 'audio',
    summary: (p) => `${str(p.action) === 'stop' ? '停止' : str(p.action) === 'fade' ? '淡出' : '播放'} ${str(p.channel)} ${str(p.assetId)}`,
    params: [
      { key: 'assetId', label: '音频素材', type: 'assetRef', required: true, assetKinds: ['audio'] },
      { key: 'channel', label: '声道', type: 'enum', required: true, enumValues: ['bgm', 'ambience', 'sefx', 'voice'], default: 'bgm' },
      { key: 'action', label: '动作', type: 'enum', required: true, enumValues: ['play', 'stop', 'fade'], default: 'play' },
      { key: 'loop', label: '循环', type: 'boolean', default: true, optional: true },
      { key: 'volume', label: '音量', type: 'number', min: 0, max: 1, default: 1, optional: true },
      { key: 'waitForEnd', label: '等待播完', type: 'boolean', default: false, optional: true },
    ],
  },
  video: {
    type: 'video', label: '视频', since: 1, runtimeSupport: 'host_required', hostCapability: 'video', waits: true,
    summary: (p) => `视频 ${str(p.assetId)}（${str(p.onEnd) === 'stop' ? '结束剧情' : '继续'}${p.skippable === false ? '，不可跳过' : '，可跳过'}）`,
    params: [
      { key: 'assetId', label: '视频素材', type: 'assetRef', required: true, assetKinds: ['video'] },
      { key: 'skippable', label: '允许跳过', type: 'boolean', default: true, optional: true },
      { key: 'onEnd', label: '播放结束后', type: 'enum', required: true, enumValues: ['continue', 'stop'], default: 'continue' },
      { key: 'onError', label: '失败后', type: 'enum', required: true, enumValues: ['continue', 'stop', 'replay'], default: 'continue' },
    ],
  },
  wait: {
    type: 'wait', label: '等待', since: 1, runtimeSupport: 'implemented', waits: true,
    summary: (p) => `等待 ${Number(p.ms ?? 0)} 毫秒`,
    params: [{ key: 'ms', label: '毫秒', type: 'number', required: true, min: 0, max: 600000, default: 500 }],
  },
  level: {
    type: 'level', label: 'HTML 关卡', since: 1, runtimeSupport: 'host_required', hostCapability: 'level', waits: true,
    summary: (p) => `关卡 ${str(p.levelId)}（${p.supportsSnapshot === false ? '读档重开' : '支持快照'}）`,
    params: [
      { key: 'levelId', label: '关卡', type: 'levelRef', required: true },
      { key: 'supportsSnapshot', label: '支持快照', type: 'boolean', default: true, optional: true },
      text('initPayload', '初始参数', { optional: true, maxLength: 2000 }),
    ],
  },
  jump: {
    type: 'jump', label: '跳转', since: 1, runtimeSupport: 'implemented',
    summary: (p) => `跳转 → ${str(p.targetSceneId) || str(p.targetEventId) || '(未设置)'}`,
    params: [
      text('targetSceneId', '目标场景', { optional: true }),
      text('targetEventId', '目标事件', { optional: true }),
    ],
  },
  ending: {
    type: 'ending', label: '结束', since: 1, runtimeSupport: 'implemented', waits: true,
    summary: (p) => `结束：${str(p.title) || str(p.endingId)}`,
    params: [
      { key: 'endingId', label: '结局 ID', type: 'text', required: true, maxLength: 64 },
      text('title', '结局标题', { maxLength: 120, optional: true }),
    ],
  },
};

export const EVENT_TYPES = Object.keys(EVENT_SPECS) as EventType[];

export function isEventType(value: unknown): value is EventType {
  return typeof value === 'string' && Object.prototype.hasOwnProperty.call(EVENT_SPECS, value);
}

export function eventSpec(type: EventType): EventSpec {
  return EVENT_SPECS[type];
}

export function eventSummary(type: string, params: Record<string, unknown>): string {
  if (!isEventType(type)) return `未知事件 ${type}`;
  return EVENT_SPECS[type].summary(params ?? {});
}

/** 事件是否需要宿主（音频/视频/关卡）配合完成；不需要宿主的事件由内核直接推进。 */
export function hostCapabilityOf(type: EventType): 'audio' | 'video' | 'level' | null {
  return EVENT_SPECS[type].hostCapability ?? null;
}

/** 选择和跳转共用的目标校验，也用于未改动场景的引用复查。 */
export function validateEventTarget(values: Record<string, unknown>, ctx: EventValidationContext, prefix = ''): ParamIssue[] {
  const issues: ParamIssue[] = [];
  const targetSceneId = str(values.targetSceneId);
  const targetEventId = str(values.targetEventId);
  const field = (name: string) => prefix ? `${prefix}.${name}` : name;
  if (targetSceneId && ctx.sceneIds && !ctx.sceneIds.includes(targetSceneId)) {
    issues.push({ code: 'SCENE_NOT_FOUND', field: field('targetSceneId'), message: `目标场景 ${targetSceneId} 不存在` });
  }
  if (targetEventId && ctx.eventIds) {
    if (!Object.values(ctx.eventIds).some((ids) => ids.includes(targetEventId))) {
      issues.push({ code: 'EVENT_NOT_FOUND', field: field('targetEventId'), message: `目标事件 ${targetEventId} 不存在` });
    } else if (targetSceneId && ctx.eventIds[targetSceneId] && !ctx.eventIds[targetSceneId].includes(targetEventId)) {
      issues.push({ code: 'EVENT_SCENE_MISMATCH', field: field('targetEventId'), message: `目标事件 ${targetEventId} 不属于场景 ${targetSceneId}` });
    }
  }
  return issues;
}

export function validateEventParams(type: string, params: unknown, ctx: EventValidationContext = {}): ParamIssue[] {
  const issues: ParamIssue[] = [];
  if (!isEventType(type)) return [{ code: 'UNKNOWN_EVENT_TYPE', message: `不支持的事件类型 ${String(type)}` }];
  if (!params || typeof params !== 'object' || Array.isArray(params)) {
    return [{ code: 'INVALID_PARAMS', message: '事件参数必须是对象' }];
  }
  const values = params as Record<string, unknown>;
  for (const spec of EVENT_SPECS[type].params) {
    const value = values[spec.key];
    if (value === undefined || value === null || value === '') {
      if (spec.required && spec.default === undefined) issues.push({ code: 'REQUIRED', field: spec.key, message: `${spec.label}不能为空` });
      continue;
    }
    switch (spec.type) {
      case 'text':
      case 'longText': {
        requireText(values, spec.key, spec.label, issues, spec.maxLength ?? 200);
        break;
      }
      case 'number': {
        if (typeof value !== 'number' || Number.isNaN(value)) issues.push({ code: 'INVALID_NUMBER', field: spec.key, message: `${spec.label}必须是数字` });
        else if (spec.min !== undefined && value < spec.min) issues.push({ code: 'OUT_OF_RANGE', field: spec.key, message: `${spec.label}不能小于 ${spec.min}` });
        else if (spec.max !== undefined && value > spec.max) issues.push({ code: 'OUT_OF_RANGE', field: spec.key, message: `${spec.label}不能大于 ${spec.max}` });
        break;
      }
      case 'boolean': {
        if (typeof value !== 'boolean') issues.push({ code: 'INVALID_BOOLEAN', field: spec.key, message: `${spec.label}必须是布尔值` });
        break;
      }
      case 'enum': {
        if (!spec.enumValues?.includes(String(value))) issues.push({ code: 'INVALID_ENUM', field: spec.key, message: `${spec.label}必须是 ${spec.enumValues?.join('/')} 之一` });
        break;
      }
      case 'assetRef': {
        const kind = ctx.assets?.[String(value)];
        if (!kind) issues.push({ code: 'ASSET_NOT_FOUND', field: spec.key, message: `${spec.label}引用的素材 ${String(value)} 不存在` });
        else if (spec.assetKinds && !spec.assetKinds.includes(kind)) issues.push({ code: 'ASSET_KIND', field: spec.key, message: `${spec.label}需要 ${spec.assetKinds.join('/')} 类型素材，${String(value)} 是 ${kind}` });
        break;
      }
      case 'sceneRef': {
        if (ctx.sceneIds && !ctx.sceneIds.includes(String(value))) issues.push({ code: 'SCENE_NOT_FOUND', field: spec.key, message: `${spec.label}引用的场景 ${String(value)} 不存在` });
        break;
      }
      case 'eventRef': {
        const all = ctx.eventIds ? Object.values(ctx.eventIds).flat() : [];
        if (!all.includes(String(value))) issues.push({ code: 'EVENT_NOT_FOUND', field: spec.key, message: `${spec.label}引用的事件 ${String(value)} 不存在` });
        break;
      }
      case 'variableRef': {
        if (ctx.variableIds && !ctx.variableIds.includes(String(value))) issues.push({ code: 'VARIABLE_NOT_FOUND', field: spec.key, message: `${spec.label}引用的变量 ${String(value)} 不存在` });
        break;
      }
      case 'levelRef': {
        if (ctx.levelIds && !ctx.levelIds.includes(String(value))) issues.push({ code: 'LEVEL_NOT_FOUND', field: spec.key, message: `${spec.label}引用的关卡 ${String(value)} 不存在` });
        break;
      }
      case 'list': {
        if (!Array.isArray(value)) {
          issues.push({ code: 'INVALID_LIST', field: spec.key, message: `${spec.label}必须是列表` });
          break;
        }
        if (type === 'choice') {
          if (value.length < 1 || value.length > 6) issues.push({ code: 'CHOICE_COUNT', field: spec.key, message: '选择需要 1–6 个选项' });
          value.forEach((item, index) => {
            if (!item || typeof item !== 'object') {
              issues.push({ code: 'INVALID_LIST_ITEM', field: `${spec.key}[${index}]`, message: '选项必须是对象' });
              return;
            }
            const option = item as Record<string, unknown>;
            const label = option.label;
            if (typeof label !== 'string' || !label.trim() || label.length > 120) {
              issues.push({ code: 'INVALID_LIST_ITEM', field: `${spec.key}[${index}].label`, message: '选项文字必须是 1–120 字' });
            }
            const targetEventId = option.targetEventId;
            const targetSceneId = option.targetSceneId;
            const hasEvent = typeof targetEventId === 'string' && targetEventId.length > 0;
            const hasScene = typeof targetSceneId === 'string' && targetSceneId.length > 0;
            if (!hasEvent && !hasScene) issues.push({ code: 'CHOICE_TARGET', field: `${spec.key}[${index}]`, message: '选项必须指定目标事件或目标场景' });
            issues.push(...validateEventTarget(option, ctx, `${spec.key}[${index}]`));
          });
        }
        break;
      }
      default:
        break;
    }
  }
  if (type === 'jump') issues.push(...validateEventTarget(values, ctx));
  return issues;
}

/** 编辑器属性表单使用的默认参数值。 */
export function defaultEventParams(type: EventType): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  for (const spec of EVENT_SPECS[type].params) {
    if (spec.default !== undefined) params[spec.key] = spec.default;
    else if (spec.type === 'list') params[spec.key] = type === 'choice' ? [{ label: '选项一', targetEventId: '' }] : [];
    else if (spec.type === 'text' || spec.type === 'longText') params[spec.key] = '';
  }
  return params;
}

export { EVENT_CATALOG_VERSION };
