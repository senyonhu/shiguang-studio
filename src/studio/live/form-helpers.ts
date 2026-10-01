import { EVENT_SPECS, validateEventParams } from '../../runtime/events.ts';
import type { EventType, ParamIssue } from '../../runtime/events.ts';
import type { AssetDoc } from '../../runtime/content.ts';
import type { Outline } from './api.ts';

/** Native numeric fields must retain an empty value so required validation can explain it. */
export function numberInputValue(raw: string): number | '' {
  return raw.trim() === '' ? '' : Number(raw);
}

/** The event format stores variable values as text; the runtime converts using the definition. */
export function variableValueText(value: unknown, type: 'boolean' | 'number' | 'string'): string {
  if (type === 'boolean') return value === true || value === 'true' ? 'true' : 'false';
  if (value === undefined || value === null) return type === 'number' ? '0' : '';
  return String(value);
}

export function eventFormIssues(
  type: EventType,
  params: Record<string, unknown>,
  assets: AssetDoc[],
  outline: Outline | null,
): ParamIssue[] {
  const issues = validateEventParams(type, params, {
    assets: Object.fromEntries(assets.map((asset) => [asset.assetId, asset.kind])),
    sceneIds: outline?.outline.flatMap((chapter) => chapter.scenes.map((scene) => scene.sceneId)),
    variableIds: outline?.variables.map((variable) => variable.variableId),
    levelIds: outline?.levels.map((level) => level.levelId),
  });

  for (const field of EVENT_SPECS[type].params) {
    if (field.type === 'number' && field.required && params[field.key] === '' && !issues.some((issue) => issue.field === field.key)) {
      issues.push({ code: 'REQUIRED', field: field.key, message: `${field.label}不能为空` });
    }
  }

  if (type === 'variable') {
    const definition = outline?.variables.find((variable) => variable.variableId === params.variableId);
    if (definition?.type === 'number' && params.value !== '' && params.value !== undefined && !Number.isFinite(Number(params.value))) {
      issues.push({ code: 'INVALID_NUMBER', field: 'value', message: '这个变量需要一个有效数字' });
    }
    if (definition && definition.type !== 'number' && params.op === 'add') {
      issues.push({ code: 'INVALID_OPERATION', field: 'op', message: '只有数值变量可以增加数值' });
    }
    if (definition?.type === 'boolean' && !['true', 'false'].includes(String(params.value))) {
      issues.push({ code: 'INVALID_BOOLEAN', field: 'value', message: '请选择是或否' });
    }
  }

  if (type === 'jump' && !params.targetSceneId && !params.targetEventId) {
    issues.push({ code: 'JUMP_TARGET', field: 'targetSceneId', message: '请选择目标场景或目标事件' });
  }
  return issues;
}
