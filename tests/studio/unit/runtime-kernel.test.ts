// 运行内核与事件目录的单元测试（纯函数，无后台进程）。
import { describe, expect, it } from 'vitest';
import { applyRuntimeAction, restoreRuntime, saveRuntime, startRuntime } from '../../../src/runtime/kernel.ts';
import type { RuntimeState } from '../../../src/runtime/kernel.ts';
import type { StoryContent } from '../../../src/runtime/content.ts';
import { validateStory } from '../../../src/runtime/content.ts';
import { defaultEventParams, eventSummary, validateEventParams } from '../../../src/runtime/events.ts';

function content(): StoryContent {
  return {
    project: {
      projectId: 'prj_test000001',
      title: '测试工程',
      formatVersion: 1,
      entrySceneId: 'sc_open000001',
      chapterIds: ['ch_one00000001'],
      createdAt: '2026-01-01T00:00:00.000Z',
      updatedAt: '2026-01-01T00:00:00.000Z',
      runtimeCompat: 'studio-runtime/1',
    },
    chapters: [{ chapterId: 'ch_one00000001', title: '第一章', order: 0, sceneIds: ['sc_open000001', 'sc_second00001'] }],
    scenes: [
      {
        sceneId: 'sc_open000001',
        chapterId: 'ch_one00000001',
        title: '开场',
        events: [
          { eventId: 'ev_bg000000001', type: 'background', params: { assetId: 'as_bg000000001' } },
          { eventId: 'ev_line0000001', type: 'dialogue', params: { speaker: '璃', text: '雨还在下。' } },
          { eventId: 'ev_choice00001', type: 'choice', params: { prompt: '去哪？', options: [{ label: '进塔', targetSceneId: 'sc_second00001' }, { label: '等待', targetEventId: 'ev_line0000001' }] } },
        ],
      },
      {
        sceneId: 'sc_second00001',
        chapterId: 'ch_one00000001',
        title: '塔内',
        events: [
          { eventId: 'ev_flag0000001', type: 'variable', params: { variableId: 'var_flag0000001', op: 'set', value: 'true' } },
          { eventId: 'ev_video000001', type: 'video', params: { assetId: 'as_video000001', skippable: true, onEnd: 'continue', onError: 'continue' } },
          { eventId: 'ev_end00000001', type: 'ending', params: { endingId: 'end_tower', title: '塔顶' } },
        ],
      },
    ],
    variables: [{ variableId: 'var_flag0000001', name: '进入塔内', type: 'boolean', initial: false, scope: 'global' }],
    assets: [
      { assetId: 'as_bg000000001', name: '雨夜', kind: 'background', path: 'assets/originals/as_bg000000001.png', mimeType: 'image/png', sha256: 'a'.repeat(64), bytes: 10, version: '1', importedAt: '2026-01-01T00:00:00.000Z', sourceKind: 'file' },
      { assetId: 'as_video000001', name: '转场', kind: 'video', path: 'assets/originals/as_video000001.mp4', mimeType: 'video/mp4', sha256: 'b'.repeat(64), bytes: 10, version: '1', importedAt: '2026-01-01T00:00:00.000Z', sourceKind: 'file' },
    ],
    characters: [],
    levels: [],
    theme: { themeId: 'default', dialogue: { textColor: '#e8eef7', fontSize: 18, align: 'left' }, menu: { accentColor: '#7fb2ff' } },
  };
}

describe('事件目录', () => {
  it('对白事件要求台词非空', () => {
    expect(validateEventParams('dialogue', { speaker: '璃', text: '' }, {})).toHaveLength(1);
    expect(validateEventParams('dialogue', { text: '雨还在下。' }, {})).toHaveLength(0);
  });

  it('素材引用必须存在且类型匹配', () => {
    const ctx = { assets: { as_bg: 'background' as const, as_ch: 'character' as const } };
    expect(validateEventParams('background', { assetId: 'as_bg' }, ctx)).toHaveLength(0);
    expect(validateEventParams('background', { assetId: 'as_ch' }, ctx)[0].code).toBe('ASSET_KIND');
    expect(validateEventParams('background', { assetId: 'as_missing' }, ctx)[0].code).toBe('ASSET_NOT_FOUND');
  });

  it('选择必须 1–6 项且每项有目标', () => {
    expect(validateEventParams('choice', { options: [] }, {})[0].code).toBe('CHOICE_COUNT');
    const issues = validateEventParams('choice', { options: [{ label: '进塔' }] }, {});
    expect(issues.some((issue) => issue.code === 'CHOICE_TARGET')).toBe(true);
    expect(validateEventParams('choice', { options: [{ label: '进塔', targetSceneId: 'sc_second00001' }] }, {})).toHaveLength(0);
  });

  it('摘要与默认参数可用于制作与界面显示', () => {
    expect(eventSummary('dialogue', { speaker: '璃', text: '雨还在下。' })).toContain('雨还在下。');
    expect(defaultEventParams('character').position).toBe('center');
    expect(eventSummary('unknown-type', {})).toContain('未知事件');
  });
});

describe('工程结构校验', () => {
  it('合法工程没有结构问题', () => {
    expect(validateStory(content())).toHaveLength(0);
  });

  it('重复 ID 与缺失引用会被报告', () => {
    const project = content();
    project.scenes[1].events[0].eventId = 'ev_line0000001';
    project.project.entrySceneId = 'sc_missing00001';
    const issues = validateStory(project);
    expect(issues.some((issue) => issue.code === 'DUPLICATE_ID')).toBe(true);
    expect(issues.some((issue) => issue.message.includes('入口场景'))).toBe(true);
  });

  it('全量与增量校验都拒绝场景和事件不匹配的目标', () => {
    const project = content();
    project.scenes[0].events.push({ eventId: 'ev_jump0000001', type: 'jump', params: { targetSceneId: 'sc_second00001', targetEventId: 'ev_line0000001' } });
    project.scenes[0].events[2].params.options = [{ label: '错误目标', targetSceneId: 'sc_second00001', targetEventId: 'ev_line0000001' }];
    for (const options of [{}, { skipEventParamsForScenes: ['sc_open000001'] }]) {
      const issues = validateStory(project, options).filter((issue) => issue.code === 'EVENT_SCENE_MISMATCH');
      expect(issues.map((issue) => issue.objectId)).toEqual(['ev_choice00001', 'ev_jump0000001']);
    }
  });
});

describe('运行内核推进', () => {
  it('依次执行背景、对白并在对白处等待推进', () => {
    const state = startRuntime(content(), 'sc_open000001');
    expect(state.status).toBe('awaitingAdvance');
    expect(state.lines).toHaveLength(1);
    expect(state.lines[0].text).toBe('雨还在下。');
    expect(state.stage.background).toBe('as_bg000000001');
  });

  it('选择分支跳转到另一场景并执行变量、视频与结束', () => {
    let state = applyRuntimeAction(content(), startRuntime(content(), 'sc_open000001'), { type: 'advance' });
    expect(state.status).toBe('awaitingChoice');
    state = applyRuntimeAction(content(), state, { type: 'choose', option: 0 });
    expect(state.sceneId).toBe('sc_second00001');
    expect(state.variables.var_flag0000001).toBe(true);
    expect(state.status).toBe('awaitingEffect');
    expect(state.pendingEffect?.kind).toBe('video');
    state = applyRuntimeAction(content(), state, { type: 'effectComplete' });
    expect(state.status).toBe('ended');
    expect(state.ended?.endingId).toBe('end_tower');
  });

  it('视频宿主失败时按 onError 继续，不会永久等待', () => {
    let state = startRuntime(content(), 'sc_second00001');
    expect(state.status).toBe('awaitingEffect');
    state = applyRuntimeAction(content(), state, { type: 'effectFailed', error: { code: 'DECODE_FAILED', message: '无法解码' } });
    expect(state.status).toBe('ended');
    expect(state.errors.some((error) => error.code === 'DECODE_FAILED')).toBe(true);
  });

  it('等待事件按时长推进，未到时间保持等待', () => {
    const project = content();
    project.scenes[0].events.unshift({ eventId: 'ev_wait0000001', type: 'wait', params: { ms: 500 } });
    let state = startRuntime(project, 'sc_open000001');
    expect(state.status).toBe('awaitingWait');
    state = applyRuntimeAction(project, state, { type: 'tick', elapsedMs: 200 });
    expect(state.status).toBe('awaitingWait');
    expect(state.pendingWaitMs).toBe(300);
    state = applyRuntimeAction(project, state, { type: 'tick', elapsedMs: 400 });
    expect(state.status).toBe('awaitingAdvance');
  });

  it('空场景直接结束', () => {
    const project = content();
    project.scenes[1].events = [];
    const state = startRuntime(project, 'sc_second00001');
    expect(state.status).toBe('ended');
    expect(state.ended?.reason).toBe('scene-end');
  });

  it('相同输入得到相同结果（编辑器试玩与展示器一致的前提）', () => {
    const first = startRuntime(content(), 'sc_open000001');
    const second = startRuntime(content(), 'sc_open000001');
    expect(JSON.stringify(first)).toBe(JSON.stringify(second));
  });

  it.each([true, false])('选择与跳转按全工程事件 ID 定位（同时指定场景：%s）', (withScene) => {
    const project = content();
    const params = { targetEventId: 'ev_end00000001', ...(withScene ? { targetSceneId: 'sc_second00001' } : {}) };
    project.scenes[0].events = [{ eventId: 'ev_jump0000001', type: 'jump', params }];
    let state = startRuntime(project, 'sc_open000001');
    expect(state.sceneId).toBe('sc_second00001');
    expect(state.eventIndex).toBe(2);
    expect(state.ended?.title).toBe('塔顶');
    expect(state.variables.var_flag0000001).toBe(false);

    project.scenes[0].events = [{ eventId: 'ev_choice00001', type: 'choice', params: { options: [{ label: '直达塔顶', ...params }] } }];
    state = applyRuntimeAction(project, startRuntime(project, 'sc_open000001'), { type: 'choose', option: 0 });
    expect(state.sceneId).toBe('sc_second00001');
    expect(state.eventIndex).toBe(2);
    expect(state.ended?.title).toBe('塔顶');
    expect(state.variables.var_flag0000001).toBe(false);
  });

  it.each(['jump', 'choice'] as const)('%s 的坏场景/事件组合明确报错', (type) => {
    const project = content();
    const target = { targetSceneId: 'sc_second00001', targetEventId: 'ev_line0000001' };
    project.scenes[0].events = [
      { eventId: 'ev_line0000001', type: 'dialogue', params: { text: '开场' } },
      { eventId: 'ev_target000001', type, params: type === 'choice' ? { options: [{ label: '错误', ...target }] } : target },
    ];
    let state = applyRuntimeAction(project, startRuntime(project, 'sc_open000001'), { type: 'advance' });
    if (type === 'choice') state = applyRuntimeAction(project, state, { type: 'choose', option: 0 });
    expect(state.status).toBe('error');
    expect(state.errors.at(-1)?.code).toBe('EVENT_SCENE_MISMATCH');
    expect(state.eventIndex).toBe(1);
    expect(state.ended).toBeNull();
  });

  it('视频结束后可结束剧情，不执行后面的结局事件', () => {
    const project = content();
    project.scenes[1].events[1].params.onEnd = 'stop';
    const state = applyRuntimeAction(project, startRuntime(project, 'sc_second00001'), { type: 'effectComplete' });
    expect(state.status).toBe('ended');
    expect(state.eventIndex).toBe(1);
    expect(state.ended?.endingId).toBeNull();
    expect(state.ended?.title).toBe('视频播放后结束');
  });

  it('视频失败重播一次使用新 effectId，第二次失败记录错误后继续', () => {
    const project = content();
    project.scenes[1].events[1].params.onError = 'replay';
    let state = startRuntime(project, 'sc_second00001');
    const firstEffectId = state.pendingEffect?.effectId;
    state = applyRuntimeAction(project, state, { type: 'effectFailed', error: { code: 'FIRST_FAILURE' } });
    expect(state.status).toBe('awaitingEffect');
    expect(state.pendingEffect?.effectId).not.toBe(firstEffectId);
    expect(state.pendingEffect?.attempt).toBe(1);
    expect(state.eventIndex).toBe(1);
    state = applyRuntimeAction(project, state, { type: 'effectFailed', error: { code: 'SECOND_FAILURE' } });
    expect(state.status).toBe('ended');
    expect(state.ended?.title).toBe('塔顶');
    expect(state.errors.map((error) => error.code)).toEqual(['FIRST_FAILURE', 'SECOND_FAILURE']);
  });
});

describe('关卡消息与读档', () => {
  function levelContent(): StoryContent {
    const project = content();
    project.levels = [{ levelId: 'lv_star0000001', title: '星图', entry: 'levels/star.html', supportsSnapshot: true }];
    project.scenes[1].events = [
      { eventId: 'ev_level000001', type: 'level', params: { levelId: 'lv_star0000001', supportsSnapshot: true } },
      { eventId: 'ev_end00000001', type: 'ending', params: { endingId: 'end_tower', title: '塔顶' } },
    ];
    return project;
  }

  it('重复完成消息不重复推进', () => {
    let state = startRuntime(levelContent(), 'sc_second00001');
    expect(state.status).toBe('awaitingEffect');
    state = applyRuntimeAction(levelContent(), state, { type: 'levelMessage', message: { kind: 'complete', requestId: 'req-1', payload: { score: 1 } } });
    expect(state.status).toBe('ended');
    const after = applyRuntimeAction(levelContent(), state, { type: 'levelMessage', message: { kind: 'complete', requestId: 'req-1', payload: { score: 2 } } });
    expect(after.status).toBe('ended');
    expect(after.levels.completedRequests).toEqual(['req-1']);
  });

  it('检查点快照被保存，且迟到检查点不会覆盖已保存结果', () => {
    let state = startRuntime(levelContent(), 'sc_second00001');
    state = applyRuntimeAction(levelContent(), state, { type: 'levelMessage', message: { kind: 'checkpoint', requestId: 'cp-1', messageSeq: 2, payload: { angle: 30 } } });
    expect(state.levels.snapshot).toEqual({ angle: 30 });
    state = applyRuntimeAction(levelContent(), state, { type: 'levelMessage', message: { kind: 'checkpoint', requestId: 'cp-0', messageSeq: 1, payload: { angle: 99 } } });
    expect(state.levels.snapshot).toEqual({ angle: 30 });
    state = applyRuntimeAction(levelContent(), state, { type: 'levelMessage', message: { kind: 'checkpoint', requestId: 'cp-1', messageSeq: 2, payload: { angle: 99 } } });
    expect(state.levels.snapshot).toEqual({ angle: 30 });
  });

  it('下一关的新实例从序号 1 开始，检查点与完成仍能生效', () => {
    const project = levelContent();
    project.levels.push({ levelId: 'lv_next0000001', title: '下一关', entry: 'levels/next.html', supportsSnapshot: true });
    project.scenes[1].events.splice(1, 0, { eventId: 'ev_next0000001', type: 'level', params: { levelId: 'lv_next0000001', supportsSnapshot: true } });
    let state = startRuntime(project, 'sc_second00001');
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'ready', levelInstanceId: 'instance-first', messageSeq: 1 } });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'checkpoint', levelInstanceId: 'instance-first', requestId: 'first-8', messageSeq: 8, payload: { source: 'first draft' } } });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'complete', levelInstanceId: 'instance-first', requestId: 'first-9', messageSeq: 9, payload: { passed: true } } });
    expect(state.pendingEffect?.payload.levelId).toBe('lv_next0000001');
    expect(state.levels.lastMessageSeq).toBe(0);
    expect(state.levels.snapshot).toBeNull();
    expect(state.pendingEffect?.payload.snapshot).toBeNull();
    expect(state.levels.result).toBeNull();
    expect(state.levels.levelInstanceId).toBeNull();
    expect(state.levels.completedRequests).toEqual([]);

    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'ready', levelInstanceId: 'instance-next', messageSeq: 1 } });
    expect(state.levels.lastMessageSeq).toBe(1);
    expect(state.levels.completedRequests).toEqual([]);
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'checkpoint', levelInstanceId: 'instance-next', requestId: 'next-2', messageSeq: 2, payload: { source: 'next draft' } } });
    expect(state.levels.snapshot).toEqual({ source: 'next draft' });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'complete', levelInstanceId: 'instance-next', requestId: 'next-3', messageSeq: 3, payload: { passed: true, level: 'next' } } });
    expect(state.status).toBe('ended');
    expect(state.levels.result).toEqual({ passed: true, level: 'next' });
  });

  it('读档保留源码快照，新实例重置序号后能继续保存并完成', () => {
    const project = levelContent();
    let state = startRuntime(project, 'sc_second00001', { revision: 'rev-level' });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'ready', levelInstanceId: 'instance-before-save', messageSeq: 1 } });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'checkpoint', levelInstanceId: 'instance-before-save', requestId: 'saved-8', messageSeq: 8, payload: { source: 'saved draft', stdin: '-18 3' } } });
    const restored = restoreRuntime(project, saveRuntime(state, '2026-10-01T00:00:00.000Z'), 'rev-level');
    expect(restored.ok).toBe(true);
    state = restored.state!;
    expect(state.levels.lastMessageSeq).toBe(8);
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'ready', levelInstanceId: 'instance-after-load', messageSeq: 1 } });
    expect(state.levels.lastMessageSeq).toBe(1);
    expect(state.levels.snapshot).toEqual({ source: 'saved draft', stdin: '-18 3' });
    expect(state.levels.completedRequests).toEqual([]);
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'checkpoint', levelInstanceId: 'instance-after-load', requestId: 'loaded-2', messageSeq: 2, payload: { source: 'continued draft', stdin: '0 0' } } });
    expect(state.levels.snapshot).toEqual({ source: 'continued draft', stdin: '0 0' });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'complete', levelInstanceId: 'instance-after-load', requestId: 'loaded-3', messageSeq: 3, payload: { passed: true } } });
    expect(state.status).toBe('ended');
    expect(state.levels.result).toEqual({ passed: true });
  });

  it('同实例重复 ready 不清除去重记录，也不会重复应用完成结果', () => {
    const project = levelContent();
    let state = startRuntime(project, 'sc_second00001');
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'ready', levelInstanceId: 'instance-same', messageSeq: 1 } });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'checkpoint', levelInstanceId: 'instance-same', requestId: 'same-2', messageSeq: 2, payload: { source: 'valid draft' } } });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'ready', levelInstanceId: 'instance-same', messageSeq: 1 } });
    expect(state.levels.lastMessageSeq).toBe(2);
    expect(state.levels.completedRequests).toEqual(['same-2']);
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'checkpoint', levelInstanceId: 'instance-same', requestId: 'same-2', messageSeq: 2, payload: { source: 'duplicate draft' } } });
    expect(state.levels.snapshot).toEqual({ source: 'valid draft' });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'complete', levelInstanceId: 'instance-same', requestId: 'same-3', messageSeq: 3, payload: { score: 1 } } });
    expect(state.status).toBe('ended');
    const steps = state.steps;
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'ready', levelInstanceId: 'instance-same', messageSeq: 1 } });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'complete', levelInstanceId: 'instance-same', requestId: 'same-3', messageSeq: 3, payload: { score: 99 } } });
    expect(state.levels.lastMessageSeq).toBe(3);
    expect(state.levels.completedRequests).toEqual(['same-2', 'same-3']);
    expect(state.levels.result).toEqual({ score: 1 });
    expect(state.steps).toBe(steps);
  });

  it('读档要求内核版本与修订一致', () => {
    const recorded = saveRuntime(startRuntime(content(), 'sc_open000001', { revision: 'rev-1' }), '2026-01-01T00:00:00.000Z');
    expect(restoreRuntime(content(), recorded, 'rev-1').ok).toBe(true);
    expect(restoreRuntime(content(), { ...recorded, revision: 'other' }, 'rev-1').ok).toBe(false);
    expect(restoreRuntime(content(), { ...recorded, kernelVersion: 99 }, 'rev-1').reason).toContain('内核版本');
  });

  it('读档恢复到对白等待位置', () => {
    const state: RuntimeState = applyRuntimeAction(content(), startRuntime(content(), 'sc_open000001'), { type: 'advance' });
    const record = saveRuntime(state, '2026-01-01T00:00:00.000Z');
    const restored = restoreRuntime(content(), record, record.revision);
    expect(restored.ok).toBe(true);
    expect(restored.state?.status).toBe(state.status);
    expect(restored.state?.lines.length).toBe(state.lines.length);
  });

  it('普通对白读档保持当前台词，推进后只追加一次新台词', () => {
    const project = content();
    project.scenes[0].events[2] = { eventId: 'ev_line0000002', type: 'dialogue', params: { text: '第二句。' } };
    const record = saveRuntime(startRuntime(project, 'sc_open000001'), 'now');
    let state = restoreRuntime(project, record, '').state!;
    expect(state.status).toBe('awaitingAdvance');
    expect(state.lines.map((line) => line.text)).toEqual(['雨还在下。']);
    state = applyRuntimeAction(project, state, { type: 'advance' });
    expect(state.lines.map((line) => line.text)).toEqual(['雨还在下。', '第二句。']);
  });

  it('读档保留剩余等待和结局标题', () => {
    const project = content();
    project.scenes[0].events.unshift({ eventId: 'ev_wait0000001', type: 'wait', params: { ms: 500 } });
    const waiting = applyRuntimeAction(project, startRuntime(project, 'sc_open000001'), { type: 'tick', elapsedMs: 200 });
    const restored = restoreRuntime(project, saveRuntime(waiting, 'now'), '').state!;
    expect(restored.pendingWaitMs).toBe(300);
    expect(applyRuntimeAction(project, restored, { type: 'tick', elapsedMs: 299 }).status).toBe('awaitingWait');
    expect(applyRuntimeAction(project, restored, { type: 'tick', elapsedMs: 300 }).status).toBe('awaitingAdvance');
    const ended = applyRuntimeAction(project, startRuntime(project, 'sc_second00001'), { type: 'effectComplete' });
    expect(restoreRuntime(project, saveRuntime(ended, 'now'), '').state?.ended).toEqual(ended.ended);
  });

  it('等待音频读档重新交给宿主，视频读档保留已经使用的重播次数', () => {
    const project = content();
    project.scenes[0].events = [{ eventId: 'ev_audio000001', type: 'audio', params: { assetId: 'as_voice000001', channel: 'voice', waitForEnd: true, loop: false } }];
    const audioRecord = saveRuntime(startRuntime(project, 'sc_open000001'), 'now');
    const audio = restoreRuntime(project, audioRecord, '').state!;
    expect(audio.status).toBe('awaitingEffect');
    expect(audio.pendingEffect).toEqual(audioRecord.pendingEffect);
    expect(audio.effects).toEqual([audioRecord.pendingEffect]);
    project.scenes[1].events[1].params.onError = 'replay';
    const retrying = applyRuntimeAction(project, startRuntime(project, 'sc_second00001'), { type: 'effectFailed' });
    const video = restoreRuntime(project, saveRuntime(retrying, 'now'), '').state!;
    expect(video.status).toBe('awaitingEffect');
    expect(video.pendingEffect?.attempt).toBe(1);
    expect(applyRuntimeAction(project, video, { type: 'effectFailed' }).status).toBe('ended');
  });

  it('存档和恢复均深拷贝，不修改来源状态或存档中的选择、快照', () => {
    const project = content();
    const state = applyRuntimeAction(project, startRuntime(project, 'sc_open000001'), { type: 'advance' });
    const record = saveRuntime(state, 'now');
    record.pendingChoice!.options[0].label = '存档副本';
    record.stage.background = null;
    expect(state.pendingChoice!.options[0].label).toBe('进塔');
    expect(state.stage.background).toBe('as_bg000000001');
    const restored = restoreRuntime(project, record, '').state!;
    expect(restored.pendingChoice).toEqual(record.pendingChoice);
    restored.pendingChoice!.options[0].label = '恢复副本';
    expect(record.pendingChoice!.options[0].label).toBe('存档副本');
    let levelState = startRuntime(levelContent(), 'sc_second00001');
    levelState = applyRuntimeAction(levelContent(), levelState, { type: 'levelMessage', message: { kind: 'checkpoint', payload: { nested: { source: 'saved' } } } });
    const levelRecord = saveRuntime(levelState, 'now');
    (levelRecord.levels.snapshot as { nested: { source: string } }).nested.source = 'changed';
    expect(levelState.levels.snapshot).toEqual({ nested: { source: 'saved' } });
  });

  it('即使修订相同也拒绝其他工程与没有工程 ID 的旧存档', () => {
    const project = content();
    const state = startRuntime(project, 'sc_open000001', { revision: 'rev-1' });
    const record = saveRuntime(state, 'now');
    expect(record.projectId).toBe(project.project.projectId);
    const other = content();
    other.project.projectId = 'prj_other000001';
    expect(restoreRuntime(other, record, 'rev-1').reason).toContain('其他作品');
    expect(applyRuntimeAction(other, state, { type: 'advance' }).errors.at(-1)?.code).toBe('PROJECT_MISMATCH');
    const legacy = { ...record };
    delete (legacy as Partial<typeof legacy>).projectId;
    expect(restoreRuntime(project, legacy, 'rev-1').reason).toContain('缺少作品 ID');
  });

  it('损坏的事件位置、历史、舞台和停留状态均拒绝恢复', () => {
    const project = content();
    const record = saveRuntime(startRuntime(project, 'sc_open000001'), 'now');
    const damaged = [
      { ...record, eventIndex: -1 }, { ...record, eventIndex: 0.5 }, { ...record, eventIndex: 999 },
      { ...record, lines: null }, { ...record, stage: {} }, { ...record, levels: {} },
      { ...record, pendingWaitMs: 100 }, { ...record, status: 'unknown' },
    ];
    for (const bad of damaged) {
      expect(restoreRuntime(project, bad as typeof record, '').ok).toBe(false);
    }
  });

  it('同一关卡循环进入也是新实例，旧快照和结果不会带入', () => {
    const project = levelContent();
    project.scenes[1].events[1] = { eventId: 'ev_jump0000001', type: 'jump', params: { targetEventId: 'ev_level000001' } };
    let state = startRuntime(project, 'sc_second00001');
    const firstEffectId = state.pendingEffect?.effectId;
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'ready', levelInstanceId: 'first', messageSeq: 1 } });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'checkpoint', levelInstanceId: 'first', messageSeq: 8, requestId: 'cp', payload: { source: 'first' } } });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'complete', levelInstanceId: 'first', messageSeq: 9, requestId: 'done', payload: { score: 10 } } });
    expect(state.pendingEffect?.effectId).not.toBe(firstEffectId);
    expect(state.levels).toMatchObject({ snapshot: null, result: null, levelInstanceId: null, lastMessageSeq: 0, completedRequests: [] });
    expect(state.pendingEffect?.payload.snapshot).toBeNull();
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'ready', levelInstanceId: 'second', messageSeq: 1 } });
    const afterLate = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'complete', levelInstanceId: 'first', messageSeq: 10, payload: { score: 99 } } });
    expect(afterLate).toEqual(state);
  });

  it.each(['event', 'definition'] as const)('不支持快照时，%s 声明禁止保存和下发快照', (source) => {
    const project = levelContent();
    if (source === 'event') project.scenes[1].events[0].params.supportsSnapshot = false;
    else project.levels[0].supportsSnapshot = false;
    let state = startRuntime(project, 'sc_second00001', { levelSnapshot: { forbidden: true } });
    state = applyRuntimeAction(project, state, { type: 'levelMessage', message: { kind: 'checkpoint', payload: { forbidden: true } } });
    const record = saveRuntime(state, 'now');
    expect(record.levels.snapshot).toBeNull();
    expect(record.pendingEffect?.payload.snapshot).toBeNull();
    record.levels.snapshot = { injected: true };
    record.pendingEffect!.payload.snapshot = { injected: true };
    const restored = restoreRuntime(project, record, '').state!;
    expect(restored.levels.snapshot).toBeNull();
    expect(restored.pendingEffect?.payload.snapshot).toBeNull();
    expect(restored.pendingEffect?.payload.supportsSnapshot).toBe(false);
  });
});
