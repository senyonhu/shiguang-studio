import { describe, expect, it } from 'vitest';
import { eventFormIssues, numberInputValue, variableValueText } from '../../../src/studio/live/form-helpers.ts';
import { startRuntime } from '../../../src/runtime/kernel.ts';
import { DEFAULT_THEME } from '../../../src/runtime/content.ts';
import type { Outline } from '../../../src/studio/live/api.ts';
import type { StoryContent } from '../../../src/runtime/content.ts';

const outline: Outline = {
  revision: '0', outline: [{ chapterId: 'ch_one', title: '第一章', scenes: [{ sceneId: 'sc_one', title: '开场', eventCount: 1 }] }],
  characters: [], levels: [],
  variables: [
    { variableId: 'var_number', name: '灯芯数量', type: 'number', initial: 3, scope: 'global' },
    { variableId: 'var_bool', name: '门已打开', type: 'boolean', initial: false, scope: 'global' },
    { variableId: 'var_text', name: '姓名', type: 'string', initial: '', scope: 'global' },
  ],
};

describe('human event form contracts', () => {
  it('keeps dialogue text that resembles JSON valid and unchanged', () => {
    for (const text of ['123', 'true', '"你好"', '{"label":"对白"}']) {
      const params = { speaker: '', text };
      expect(eventFormIssues('dialogue', params, [], outline)).toEqual([]);
      expect(params.text).toBe(text);
    }
  });

  it('does not silently turn an emptied numeric field into zero', () => {
    expect(numberInputValue('')).toBe('');
    expect(eventFormIssues('wait', { ms: numberInputValue('') }, [], outline)).toContainEqual(expect.objectContaining({ field: 'ms', code: 'REQUIRED' }));
    expect(numberInputValue('0')).toBe(0);
  });

  it('reports invalid variable numbers, booleans, and nonnumeric additions before submission', () => {
    expect(eventFormIssues('variable', { variableId: 'var_number', op: 'set', value: 'abc' }, [], outline)).toContainEqual(expect.objectContaining({ field: 'value', code: 'INVALID_NUMBER' }));
    expect(eventFormIssues('variable', { variableId: 'var_bool', op: 'set', value: 'yes' }, [], outline)).toContainEqual(expect.objectContaining({ field: 'value', code: 'INVALID_BOOLEAN' }));
    expect(eventFormIssues('variable', { variableId: 'var_text', op: 'add', value: '1' }, [], outline)).toContainEqual(expect.objectContaining({ field: 'op', code: 'INVALID_OPERATION' }));
  });

  it('requires a real branch destination and rejects unavailable scene references', () => {
    expect(eventFormIssues('choice', { options: [{ label: '打开门' }] }, [], outline)).toContainEqual(expect.objectContaining({ code: 'CHOICE_TARGET' }));
    expect(eventFormIssues('choice', { options: [{ label: '打开门', targetSceneId: 'sc_missing' }] }, [], outline)).toContainEqual(expect.objectContaining({ code: 'SCENE_NOT_FOUND' }));
    expect(eventFormIssues('choice', { options: [{ label: '打开门', targetSceneId: 'sc_one' }] }, [], outline)).toEqual([]);
    expect(eventFormIssues('jump', { targetSceneId: '', targetEventId: '' }, [], outline)).toContainEqual(expect.objectContaining({ code: 'JUMP_TARGET' }));
  });

  it('uses format-compatible variable text that becomes the correct runtime value type', () => {
    const updates = [
      { variableId: 'var_number', value: variableValueText(12, 'number') },
      { variableId: 'var_bool', value: variableValueText(true, 'boolean') },
      { variableId: 'var_text', value: variableValueText('123', 'string') },
    ];
    const content: StoryContent = {
      project: { projectId: 'pr_one', title: '表单验证', formatVersion: 2, entrySceneId: 'sc_one', chapterIds: ['ch_one'], createdAt: '', updatedAt: '', runtimeCompat: '' },
      chapters: [{ chapterId: 'ch_one', title: '第一章', order: 0, sceneIds: ['sc_one'] }],
      scenes: [{ sceneId: 'sc_one', chapterId: 'ch_one', title: '开场', events: updates.map((update, index) => ({ eventId: `ev_${index}`, type: 'variable', params: { ...update, op: 'set' } })) }],
      assets: [], characters: [], levels: [], variables: outline.variables, theme: DEFAULT_THEME,
    };
    for (const update of updates) expect(eventFormIssues('variable', { ...update, op: 'set' }, [], outline)).toEqual([]);
    const state = startRuntime(content, 'sc_one');
    expect(state.variables.var_number).toBe(12);
    expect(state.variables.var_bool).toBe(true);
    expect(state.variables.var_text).toBe('123');
  });
});
