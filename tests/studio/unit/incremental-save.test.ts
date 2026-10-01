// 增量保存与内容缓存的正确性测试：
//  1. verifyPlan 打开时，增量保存计划必须与全量序列化计划完全一致（防止 changedIds 映射遗漏导致漏写）。
//  2. 覆盖各类动作（章节/场景/事件/素材/变量/角色/关卡/删除/撤销）后，新实例从磁盘读到的内容必须与内存一致。
//  3. 外部直接改动工程文件时缓存必须失效（磁盘指纹变化）。
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EditService } from '../../../server/src/edit/service.ts';
import type { ActorRef } from '../../../server/src/edit/service.ts';
import { createProject } from '../../../server/src/store/workspace.ts';
import { loadProject } from '../../../server/src/store/doc.ts';
import { projectPaths } from '../../../server/src/store/paths.ts';
import { loadConfig } from '../../../server/src/store/config.ts';
import { encodeSolidPng } from '../../../server/src/media/decode.ts';

let root = '';
let service: EditService;
let actor: ActorRef;
let projectId = '';
let chapterId = '';
let sceneId = '';

const AI: ActorRef = { actorId: 'ai:incr', label: 'incr', sessionId: 'sess_incr000001', channel: 'mcp', scopes: ['read', 'edit'] };

beforeEach(() => {
  root = mkdtempSync(path.join(os.tmpdir(), 'studio-incr-'));
  const config = loadConfig(root);
  mkdirSync(path.join(root, 'imports'), { recursive: true });
  // verifyPlan：每次提交都比对增量计划与全量计划，任何映射遗漏都会让用例失败
  service = new EditService({ workspaceRoot: root, importRoots: config.importRoots, verifyPlan: true, now: () => '2026-03-03T00:00:00.000Z' });
  actor = { ...AI };
  const created = createProject(root, { title: '增量保存测试', chapterTitle: '第一章', sceneTitle: '开场' });
  projectId = created.projectId;
  chapterId = service.snapshot(projectId).content.chapters[0].chapterId;
  sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

function apply(requestId: string, operations: { action: string; payload?: Record<string, unknown>; targetIds?: string[]; expectedObjectRevision?: string }[], baseRevision?: string) {
  const revision = baseRevision ?? service.snapshot(projectId).revision;
  try {
    return service.applyTransaction(actor, { requestId, projectId, baseRevision: revision, operations });
  } catch (error) {
    const details = (error as { details?: unknown }).details;
    throw new Error(`${requestId} 提交异常：${(error as Error).message}${details ? ` :: ${JSON.stringify(details)}` : ''}`);
  }
}

describe('增量保存计划与全量计划一致（verifyPlan）', () => {
  it('覆盖各类动作后都不会漏写文件', () => {
    const chapter = apply('incr-1', [{ action: 'chapter.create', payload: { title: '第二章', chapterId: 'ch_incr00000001' } }]);
    expect(chapter.status).toBe('applied');

    const scene = apply('incr-2', [{ action: 'scene.create', payload: { chapterId: 'ch_incr00000001', title: '新场景', sceneId: 'sc_incr00000001' } }]);
    expect(scene.status).toBe('applied');

    expect(apply('incr-3', [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '一句台词' }, eventId: 'ev_incr00000001' } }]).status).toBe('applied');
    expect(apply('incr-4', [{ action: 'event.update', payload: { sceneId, eventId: 'ev_incr00000001', params: { speaker: '璃', text: '改过的台词' } } }]).status).toBe('applied');
    expect(apply('incr-5', [{ action: 'event.insert', payload: { sceneId, type: 'wait', params: { ms: 100 }, eventId: 'ev_incr00000002' } }]).status).toBe('applied');
    expect(apply('incr-6', [{ action: 'event.move', payload: { sceneId, eventId: 'ev_incr00000002', index: 0 } }]).status).toBe('applied');

    const png = path.join(root, 'imports', 'bg.png');
    writeFileSync(png, encodeSolidPng(4, 3, [9, 9, 9]));
    expect(apply('incr-7', [{ action: 'asset.registerFromFile', payload: { sourcePath: png, name: '璃的立绘', kind: 'character', assetId: 'as_incr00000001' } }]).status).toBe('applied');
    expect(apply('incr-8', [{ action: 'asset.bind', targetIds: ['ev_incr00000001'], payload: { sceneId, eventId: 'ev_incr00000001', field: 'characterId', assetId: 'as_incr00000001' } }]).status).toBe('applied');

    expect(apply('incr-9', [{ action: 'variable.create', payload: { name: '进入塔内', type: 'boolean', initial: false, variableId: 'var_incr00000001' } }]).status).toBe('applied');
    expect(apply('incr-10', [{ action: 'variable.update', payload: { variableId: 'var_incr00000001', initial: true } }]).status).toBe('applied');
    expect(apply('incr-11', [{ action: 'event.insert', payload: { sceneId, type: 'variable', params: { variableId: 'var_incr00000001', op: 'set', value: 'true' }, eventId: 'ev_incr00000003' } }]).status).toBe('applied');
    expect(apply('incr-12', [{ action: 'character.create', payload: { name: '璃', characterId: 'chr_incr0000001' } }]).status).toBe('applied');
    expect(apply('incr-13', [{ action: 'character.update', payload: { characterId: 'chr_incr0000001', description: '塔中的少女' } }]).status).toBe('applied');
    expect(apply('incr-14', [{ action: 'level.register', payload: { levelId: 'lv_incr00000001', title: '回声关卡', entry: 'levels/lv_incr00000001.html' } }]).status).toBe('applied');
    expect(apply('incr-15', [{ action: 'project.update', payload: { title: '改名后的工程' } }]).status).toBe('applied');
    expect(apply('incr-16', [{ action: 'scene.update', payload: { sceneId: 'sc_incr00000001', title: '改名场景' } }]).status).toBe('applied');
    expect(apply('incr-17', [{ action: 'chapter.update', payload: { chapterId: 'ch_incr00000001', title: '改名章节' } }]).status).toBe('applied');

    // 删除：先解除引用，再删对象（都会被 verifyPlan 校验）
    expect(apply('incr-18', [{ action: 'event.delete', payload: { sceneId, eventId: 'ev_incr00000003' } }]).status).toBe('applied');
    expect(apply('incr-19', [{ action: 'variable.delete', payload: { variableId: 'var_incr00000001' } }]).status).toBe('applied');
    expect(apply('incr-20', [{ action: 'character.delete', payload: { characterId: 'chr_incr0000001' } }]).status).toBe('applied');
    expect(apply('incr-21', [{ action: 'level.delete', payload: { levelId: 'lv_incr00000001' } }]).status).toBe('applied');
    expect(apply('incr-22', [{ action: 'scene.delete', payload: { sceneId: 'sc_incr00000001' } }]).status).toBe('applied');
    expect(apply('incr-23', [{ action: 'chapter.delete', payload: { chapterId: 'ch_incr00000001' } }]).status).toBe('applied');

    // 撤销（补偿操作同样走 verifyPlan）
    const undo = service.undo(actor, { requestId: 'incr-undo', projectId, undoId: (apply('incr-24', [{ action: 'event.insert', payload: { sceneId, type: 'wait', params: { ms: 5 }, eventId: 'ev_incr00000004' } }]) as { undoId?: string }).undoId! });
    expect(undo.status).toBe('applied');
  });

  it('素材二进制文件也按增量写入并在磁盘上存在', () => {
    const png = path.join(root, 'imports', 'bg2.png');
    writeFileSync(png, encodeSolidPng(5, 5, [1, 2, 3]));
    expect(apply('incr-bin', [{ action: 'asset.registerFromFile', payload: { sourcePath: png, name: '背景2', kind: 'background', assetId: 'as_incr00000002' } }]).status).toBe('applied');
    const assets = service.snapshot(projectId).content.assets;
    const asset = assets.find((item) => item.assetId === 'as_incr00000002')!;
    expect(readFileSync(path.join(root, projectId, asset.path)).length).toBeGreaterThan(0);
  });
});

describe('增量校验不能放过跨场景引用失效（未变更场景也要检查）', () => {
  it('删除被其它场景跳转引用的场景会被拒绝（引用方场景未变更）', () => {
    // 场景 2 + 场景 1 中的跳转（引用方场景在此之后不再变更）
    expect(apply('ref-1', [{ action: 'scene.create', payload: { chapterId, title: '目标场景', sceneId: 'sc_ref0000000001' } }]).status).toBe('applied');
    expect(apply('ref-2', [{ action: 'event.insert', payload: { sceneId, type: 'jump', params: { targetSceneId: 'sc_ref0000000001' }, eventId: 'ev_ref00000001' } }]).status).toBe('applied');
    // 先做一次无关变更，确保缓存处于“命中”状态（增量校验路径）
    expect(apply('ref-3', [{ action: 'event.insert', payload: { sceneId: 'sc_ref0000000001', type: 'wait', params: { ms: 10 }, eventId: 'ev_ref00000002' } }]).status).toBe('applied');
    expect(service.cacheStats().hits).toBeGreaterThan(0);

    const rejected = apply('ref-4', [{ action: 'scene.delete', payload: { sceneId: 'sc_ref0000000001' } }]);
    expect(rejected.status).toBe('rejected');
    expect(rejected.issues.some((issue) => issue.code === 'SCENE_NOT_FOUND')).toBe(true);
    // 目标场景仍然存在
    expect(service.snapshot(projectId).content.scenes.some((scene) => scene.sceneId === 'sc_ref0000000001')).toBe(true);
  });

  it('删除被选项引用的目标事件会被拒绝（选项所在场景未变更）', () => {
    expect(apply('choice-1', [{ action: 'scene.create', payload: { chapterId, title: '分支场景', sceneId: 'sc_ref0000000002' } }]).status).toBe('applied');
    expect(apply('choice-2', [{ action: 'event.insert', payload: { sceneId: 'sc_ref0000000002', type: 'dialogue', params: { speaker: '璃', text: '分支目标' }, eventId: 'ev_ref00000003' } }]).status).toBe('applied');
    expect(apply('choice-3', [{ action: 'event.insert', payload: { sceneId, type: 'choice', params: { prompt: '去哪', options: [{ label: '分支', targetEventId: 'ev_ref00000003' }] }, eventId: 'ev_ref00000004' } }]).status).toBe('applied');
    // 让缓存命中（引用方场景 scene 不再变更）
    expect(apply('choice-4', [{ action: 'event.insert', payload: { sceneId: 'sc_ref0000000002', type: 'wait', params: { ms: 20 }, eventId: 'ev_ref00000005' } }]).status).toBe('applied');

    const rejected = apply('choice-5', [{ action: 'event.delete', payload: { sceneId: 'sc_ref0000000002', eventId: 'ev_ref00000003' } }]);
    expect(rejected.status).toBe('rejected');
    expect(rejected.issues.some((issue) => issue.code === 'EVENT_NOT_FOUND')).toBe(true);
  });

  it('变更场景仍然做完整参数校验', () => {
    const rejected = apply('param-1', [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '' }, eventId: 'ev_ref00000006' } }]);
    expect(rejected.status).toBe('rejected');
    expect(rejected.issues.some((issue) => issue.code === 'REQUIRED' || issue.code === 'VALIDATION_FAILED')).toBe(true);
  });
});

describe('缓存不能掩盖漏写，也不能忽略外部改动', () => {
  it('经过缓存写入后，新实例（空缓存）从磁盘读到相同内容', () => {
    apply('cache-1', [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '缓存一致性' }, eventId: 'ev_cache00000001' } }]);
    const viaCache = service.snapshot(projectId).content;
    const fresh = new EditService({ workspaceRoot: root, importRoots: [path.join(root, 'imports')] });
    const fromDisk = fresh.snapshot(projectId).content;
    expect(JSON.stringify(fromDisk)).toBe(JSON.stringify(viaCache));
    expect(fromDisk.scenes[0].events[0].params.text).toBe('缓存一致性');
  });

  it('命中缓存后 stats 记录命中；外部改动会让缓存失效', () => {
    service.snapshot(projectId);
    service.snapshot(projectId);
    const stats = service.cacheStats();
    expect(stats.hits).toBeGreaterThan(0);

    // 外部直接改动（模拟用户手工编辑文件）：缓存指纹变化后必须重新读取
    const sceneFile = path.join(root, projectId, 'story', 'scenes', `${sceneId}.json`);
    const scene = JSON.parse(readFileSync(sceneFile, 'utf8')) as { title: string };
    scene.title = '外部改名的场景';
    writeFileSync(sceneFile, `${JSON.stringify(scene, null, 2)}\n`, 'utf8');
    const after = service.snapshot(projectId).content;
    expect(after.scenes.find((item) => item.sceneId === sceneId)?.title).toBe('外部改名的场景');
  });

  it('disableCache 时每次从磁盘读取（用于对照与排查）', () => {
    const uncached = new EditService({ workspaceRoot: root, importRoots: [path.join(root, 'imports')], disableCache: true });
    uncached.snapshot(projectId);
    uncached.snapshot(projectId);
    expect(uncached.cacheStats().hits).toBe(0);
  });
});
