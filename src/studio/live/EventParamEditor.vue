<script setup lang="ts">
import { computed } from 'vue';
import { EVENT_SPECS } from '../../runtime/events.ts';
import type { EventType, ParamSpec } from '../../runtime/events.ts';
import type { AssetDoc } from '../../runtime/content.ts';
import type { Outline, SceneDetail } from './api.ts';
import { eventFormIssues, numberInputValue, variableValueText } from './form-helpers.ts';

const props = defineProps<{
  type: EventType;
  modelValue: Record<string, unknown>;
  assets: AssetDoc[];
  outline: Outline | null;
  scene: SceneDetail | null;
  disabled?: boolean;
}>();
const emit = defineEmits<{ 'update:modelValue': [value: Record<string, unknown>] }>();

const spec = computed(() => EVENT_SPECS[props.type]);
const issues = computed(() => eventFormIssues(props.type, props.modelValue, props.assets, props.outline));
const sceneOptions = computed(() => props.outline?.outline.flatMap((chapter) => chapter.scenes.map((scene) => ({
  value: scene.sceneId,
  label: `${chapter.title} / ${scene.title}`,
}))) ?? []);
const selectedVariable = computed(() => props.outline?.variables.find((variable) => variable.variableId === props.modelValue.variableId));
const choices = computed<Record<string, unknown>[]>(() => Array.isArray(props.modelValue.options)
  ? props.modelValue.options.map((option) => option && typeof option === 'object' ? option as Record<string, unknown> : { label: '' })
  : []);

const assetKindLabels: Record<AssetDoc['kind'], string> = { background: '背景', character: '立绘', cg: '插图', audio: '音频', video: '视频' };
const enumLabels: Record<string, string> = {
  enter: '入场', exit: '退场', move: '移动', left: '左侧', center: '居中', right: '右侧',
  set: '设为', add: '增加', bgm: '背景音乐', ambience: '环境音', sefx: '音效', voice: '语音',
  play: '播放', fade: '淡出', continue: '继续剧情', replay: '重新播放',
};

function textValue(key: string): string {
  const value = props.modelValue[key];
  return value === undefined || value === null ? '' : String(value);
}

function patch(key: string, value: unknown): void {
  emit('update:modelValue', { ...props.modelValue, [key]: value });
}

function fieldError(key: string): string {
  return issues.value.find((issue) => issue.field === key || issue.field?.startsWith(`${key}[`))?.message ?? '';
}

function assetsFor(param: ParamSpec): AssetDoc[] {
  return props.assets.filter((asset) => !param.assetKinds || param.assetKinds.includes(asset.kind));
}

function enumLabel(key: string, value: string): string {
  if (value === 'stop') return props.type === 'video' ? '结束剧情' : '停止';
  if (key === 'align') return value === 'left' ? '左对齐' : '居中';
  return enumLabels[value] ?? value;
}

function chooseVariable(variableId: string): void {
  const variable = props.outline?.variables.find((item) => item.variableId === variableId);
  emit('update:modelValue', {
    ...props.modelValue,
    variableId,
    value: variable ? variableValueText(variable.initial, variable.type) : '',
    op: variable?.type === 'number' ? props.modelValue.op ?? 'set' : 'set',
  });
}

function selectJumpScene(targetSceneId: string): void {
  emit('update:modelValue', { ...props.modelValue, targetSceneId, targetEventId: '' });
}

function eventsFor(targetSceneId: unknown): SceneDetail['events'] {
  return !targetSceneId || targetSceneId === props.scene?.scene.sceneId ? props.scene?.events ?? [] : [];
}

function eventLabel(event: SceneDetail['events'][number]): string {
  return `${event.index + 1}. ${EVENT_SPECS[event.type].label} · ${event.summary}`;
}

function unknownScene(value: unknown): boolean {
  return typeof value === 'string' && value !== '' && !sceneOptions.value.some((option) => option.value === value);
}

function unknownEvent(value: unknown, targetSceneId: unknown): boolean {
  return typeof value === 'string' && value !== '' && !eventsFor(targetSceneId).some((event) => event.eventId === value);
}

function patchChoice(index: number, key: string, value: string): void {
  const options = choices.value.map((option, optionIndex) => optionIndex === index
    ? { ...option, [key]: value, ...(key === 'targetSceneId' ? { targetEventId: '' } : {}) }
    : { ...option });
  patch('options', options);
}

function addChoice(): void {
  patch('options', [...choices.value.map((option) => ({ ...option })), { label: '', targetSceneId: '', targetEventId: '' }]);
}

function removeChoice(index: number): void {
  patch('options', choices.value.filter((_, optionIndex) => optionIndex !== index).map((option) => ({ ...option })));
}

defineExpose({ issues });
</script>

<template>
  <div class="event-param-editor">
    <div v-for="param in spec.params" :key="param.key" class="field">
      <label :for="`param-${param.key}`">{{ param.key === 'endingId' ? '结局标记（自定义）' : param.label }}{{ param.required ? ' *' : '' }}</label>

      <div v-if="type === 'choice' && param.key === 'options'" id="param-options" data-testid="param-options" :aria-invalid="Boolean(fieldError('options'))" aria-describedby="param-options-help param-options-error">
        <p id="param-options-help" class="muted">填写选项文字，并选择目标场景或事件。可以添加 1–6 个选项。</p>
        <fieldset v-for="(option, index) in choices" :key="index" class="choice-option" :disabled="disabled" :data-testid="`choice-option-${index}`">
          <legend>选项 {{ index + 1 }}</legend>
          <label :for="`choice-${index}-label`">选项文字 *</label>
          <input :id="`choice-${index}-label`" :data-testid="`choice-label-${index}`" type="text" maxlength="120" :value="String(option.label ?? '')" @input="patchChoice(index, 'label', ($event.target as HTMLInputElement).value)" />
          <label :for="`choice-${index}-scene`">目标场景</label>
          <select :id="`choice-${index}-scene`" :data-testid="`choice-scene-${index}`" :value="String(option.targetSceneId ?? '')" @change="patchChoice(index, 'targetSceneId', ($event.target as HTMLSelectElement).value)">
            <option value="">（不跨场景，选择下方事件）</option>
            <option v-if="unknownScene(option.targetSceneId)" :value="String(option.targetSceneId)">原目标已不可用，请重新选择</option>
            <option v-for="target in sceneOptions" :key="target.value" :value="target.value">{{ target.label }}</option>
          </select>
          <label :for="`choice-${index}-event`">目标事件</label>
          <select :id="`choice-${index}-event`" :data-testid="`choice-event-${index}`" :value="String(option.targetEventId ?? '')" :disabled="disabled || !eventsFor(option.targetSceneId).length" @change="patchChoice(index, 'targetEventId', ($event.target as HTMLSelectElement).value)">
            <option value="">{{ option.targetSceneId ? '从目标场景开头开始' : '请选择事件' }}</option>
            <option v-if="unknownEvent(option.targetEventId, option.targetSceneId)" :value="String(option.targetEventId)">已保存的目标事件（{{ option.targetEventId }}）</option>
            <option v-for="event in eventsFor(option.targetSceneId)" :key="event.eventId" :value="event.eventId">{{ eventLabel(event) }}</option>
          </select>
          <p v-if="option.targetSceneId && option.targetSceneId !== scene?.scene.sceneId" class="muted">将前往目标场景；可直接从场景开头开始。</p>
          <button type="button" :data-testid="`choice-remove-${index}`" @click="removeChoice(index)">删除这个选项</button>
        </fieldset>
        <button type="button" data-testid="choice-add" :disabled="disabled || choices.length >= 6" @click="addChoice">添加选项</button>
      </div>

      <select v-else-if="param.type === 'assetRef'" :id="`param-${param.key}`" :data-testid="`param-${param.key}`" :value="textValue(param.key)" :disabled="disabled" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @change="patch(param.key, ($event.target as HTMLSelectElement).value)">
        <option value="">{{ assetsFor(param).length ? '请选择素材' : '暂无素材，请先导入' }}</option>
        <option v-if="textValue(param.key) && !assetsFor(param).some((asset) => asset.assetId === textValue(param.key))" :value="textValue(param.key)">原素材已不可用，请重新选择</option>
        <option v-for="asset in assetsFor(param)" :key="asset.assetId" :value="asset.assetId">{{ asset.name }}（{{ assetKindLabels[asset.kind] }}）</option>
      </select>

      <select v-else-if="param.type === 'variableRef'" :id="`param-${param.key}`" :data-testid="`param-${param.key}`" :value="textValue(param.key)" :disabled="disabled" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @change="chooseVariable(($event.target as HTMLSelectElement).value)">
        <option value="">{{ outline?.variables.length ? '请选择变量' : '暂无变量，请先新建变量' }}</option>
        <option v-for="variable in outline?.variables ?? []" :key="variable.variableId" :value="variable.variableId">{{ variable.name }}（{{ variable.type === 'boolean' ? '是 / 否' : variable.type === 'number' ? '数值' : '文字' }}）</option>
      </select>

      <select v-else-if="param.type === 'levelRef'" :id="`param-${param.key}`" :data-testid="`param-${param.key}`" :value="textValue(param.key)" :disabled="disabled" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @change="patch(param.key, ($event.target as HTMLSelectElement).value)">
        <option value="">{{ outline?.levels.length ? '请选择关卡' : '暂无关卡，请先导入关卡' }}</option>
        <option v-for="level in outline?.levels ?? []" :key="level.levelId" :value="level.levelId">{{ level.title }}</option>
      </select>

      <select v-else-if="param.key === 'targetSceneId' || param.type === 'sceneRef'" :id="`param-${param.key}`" :data-testid="`param-${param.key}`" :value="textValue(param.key)" :disabled="disabled" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @change="type === 'jump' ? selectJumpScene(($event.target as HTMLSelectElement).value) : patch(param.key, ($event.target as HTMLSelectElement).value)">
        <option value="">（不跨场景，选择目标事件）</option>
        <option v-if="unknownScene(modelValue[param.key])" :value="textValue(param.key)">原目标已不可用，请重新选择</option>
        <option v-for="target in sceneOptions" :key="target.value" :value="target.value">{{ target.label }}</option>
      </select>

      <select v-else-if="param.key === 'targetEventId' || param.type === 'eventRef'" :id="`param-${param.key}`" :data-testid="`param-${param.key}`" :value="textValue(param.key)" :disabled="disabled || !eventsFor(modelValue.targetSceneId).length" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @change="patch(param.key, ($event.target as HTMLSelectElement).value)">
        <option value="">{{ modelValue.targetSceneId ? '从目标场景开头开始' : '请选择事件' }}</option>
        <option v-if="unknownEvent(modelValue[param.key], modelValue.targetSceneId)" :value="textValue(param.key)">已保存的目标事件（{{ textValue(param.key) }}）</option>
        <option v-for="event in eventsFor(modelValue.targetSceneId)" :key="event.eventId" :value="event.eventId">{{ eventLabel(event) }}</option>
      </select>

      <select v-else-if="param.type === 'enum'" :id="`param-${param.key}`" :data-testid="`param-${param.key}`" :value="textValue(param.key)" :disabled="disabled" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @change="patch(param.key, ($event.target as HTMLSelectElement).value)">
        <option value="">请选择</option>
        <option v-for="option in param.enumValues ?? []" :key="option" :value="option" :disabled="type === 'variable' && param.key === 'op' && option === 'add' && selectedVariable?.type !== 'number'">{{ enumLabel(param.key, option) }}</option>
      </select>

      <select v-else-if="type === 'variable' && param.key === 'value' && selectedVariable?.type === 'boolean'" :id="`param-${param.key}`" :data-testid="`param-${param.key}`" :value="textValue(param.key)" :disabled="disabled" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @change="patch(param.key, ($event.target as HTMLSelectElement).value)">
        <option value="true">是</option>
        <option value="false">否</option>
      </select>

      <input v-else-if="type === 'variable' && param.key === 'value' && selectedVariable?.type === 'number'" :id="`param-${param.key}`" :data-testid="`param-${param.key}`" type="number" step="any" :value="textValue(param.key)" :disabled="disabled" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @input="patch(param.key, ($event.target as HTMLInputElement).value)" />

      <input v-else-if="param.type === 'boolean'" :id="`param-${param.key}`" :data-testid="`param-${param.key}`" type="checkbox" :checked="modelValue[param.key] === true" :disabled="disabled" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @change="patch(param.key, ($event.target as HTMLInputElement).checked)" />

      <input v-else-if="param.type === 'number'" :id="`param-${param.key}`" :data-testid="`param-${param.key}`" type="number" step="any" :min="param.min" :max="param.max" :value="textValue(param.key)" :disabled="disabled" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @input="patch(param.key, numberInputValue(($event.target as HTMLInputElement).value))" />

      <textarea v-else-if="param.type === 'longText' || param.key === 'initPayload'" :id="`param-${param.key}`" :data-testid="`param-${param.key}`" rows="4" :maxlength="param.maxLength" :value="textValue(param.key)" :disabled="disabled" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @input="patch(param.key, ($event.target as HTMLTextAreaElement).value)" />

      <input v-else :id="`param-${param.key}`" :data-testid="`param-${param.key}`" type="text" :maxlength="param.maxLength" :value="textValue(param.key)" :disabled="disabled" :aria-invalid="Boolean(fieldError(param.key))" :aria-describedby="fieldError(param.key) ? `param-${param.key}-error` : undefined" @input="patch(param.key, ($event.target as HTMLInputElement).value)" />

      <select v-if="type === 'dialogue' && param.key === 'speaker' && outline?.characters.length" data-testid="param-speaker-character" aria-label="从角色选择说话人" value="" :disabled="disabled" @change="patch('speaker', ($event.target as HTMLSelectElement).value)">
        <option value="">从角色选择说话人…</option>
        <option v-for="character in outline.characters" :key="character.characterId" :value="character.name">{{ character.name }}</option>
      </select>
      <p v-if="param.key === 'endingId'" class="muted">给结局一个便于识别的名称，例如“重逢”或“迷失”。</p>
      <p v-if="param.key === 'initPayload'" class="muted">可留空；内容会原样传给关卡。</p>
      <p v-if="fieldError(param.key)" :id="`param-${param.key}-error`" class="field-error" role="status">{{ fieldError(param.key) }}</p>
    </div>
  </div>
</template>

<style scoped>
.choice-option { display: grid; gap: 6px; margin: 10px 0; padding: 12px; border: 1px solid #39485c; border-radius: 8px; min-width: 0; }
.choice-option legend { padding: 0 5px; font-weight: 600; }
.choice-option label { font-size: 12px; color: #c4d1e1; }
.choice-option select, .choice-option input { box-sizing: border-box; width: 100%; min-width: 0; }
.choice-option button { justify-self: start; }
.field-error { margin: 5px 0 0; color: #ffb9af; font-size: 12px; }
[aria-invalid="true"] { border-color: #d6786b; }
</style>
