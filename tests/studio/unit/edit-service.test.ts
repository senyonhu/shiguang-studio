// 统一编辑服务单元测试：动作、事务语义、幂等、冲突、撤销、素材授权、检查点。
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { EditService } from '../../../server/src/edit/service.ts';
import type { ActorRef } from '../../../server/src/edit/service.ts';
import { createProject, listProjects } from '../../../server/src/store/workspace.ts';
import { loadConfig } from '../../../server/src/store/config.ts';
import { projectPaths } from '../../../server/src/store/paths.ts';
import { loadProject } from '../../../server/src/store/doc.ts';
import { readHistory } from '../../../server/src/store/journal.ts';
import { StudioError } from '../../../server/src/core/errors.ts';
import { encodeSolidPng } from '../../../server/src/media/decode.ts';
import { writeFileSync as writeFile } from 'node:fs';

let root = '';
let service: EditService;
let actor: ActorRef;

const AI: ActorRef = { actorId: 'ai:tester', label: 'tester', sessionId: 'sess_tester0001', channel: 'mcp', scopes: ['read', 'edit', 'generate', 'preview', 'export'] };
const READONLY: ActorRef = { actorId: 'ai:readonly', label: 'readonly', sessionId: 'sess_readonly01', channel: 'mcp', scopes: ['read'] };

beforeEach(() => {
  root = mkdtempSync(path.join(os.tmpdir(), 'studio-test-'));
  const config = loadConfig(root);
  mkdirSync(config.importRoots[0], { recursive: true });
  service = new EditService({ workspaceRoot: root, importRoots: config.importRoots, now: () => '2026-02-02T03:04:05.000Z' });
  actor = { ...AI };
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

function newProject(): string {
  const created = createProject(root, { title: '雨夜旧塔', chapterTitle: '第一章', sceneTitle: '塔前' });
  return created.projectId;
}

describe('工程创建与列举', () => {
  it('创建工程后可以列出，并带有默认章节与入口场景', () => {
    const projectId = newProject();
    const projects = listProjects(root);
    expect(projects).toHaveLength(1);
    expect(projects[0].projectId).toBe(projectId);
    expect(projects[0].revision).toBe('0');
    expect(projects[0].chapterCount).toBe(1);
    expect(projects[0].sceneCount).toBe(1);
    const snapshot = service.snapshot(projectId);
    expect(snapshot.content.project.entrySceneId).toBe(snapshot.content.scenes[0].sceneId);
  });
});

describe('事务提交', () => {
  it('插入对白后修订递增，内容落盘', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    const result = service.applyTransaction(actor, {
      requestId: 'req-dialogue-1',
      projectId,
      baseRevision: '0',
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '雨还在下。' } } }],
    });
    expect(result.status).toBe('applied');
    expect(result.revision).toBe('1');
    const reloaded = loadProject(projectPaths(root, projectId));
    expect(reloaded.content.scenes[0].events[0].params.text).toBe('雨还在下。');
  });

  it('过期基础版本返回 conflict 且不写入', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    service.applyTransaction(actor, { requestId: 'req-a', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '第一句' } } }] });
    const stale = service.applyTransaction(actor, { requestId: 'req-b', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '第二句' } } }] });
    expect(stale.status).toBe('conflict');
    expect(stale.errorCode).toBe('REVISION_CONFLICT');
    expect(stale.revision).toBe('1');
    expect(loadProject(projectPaths(root, projectId)).content.scenes[0].events).toHaveLength(1);
  });

  it('同 requestId 同内容重试返回既有结果，不重复创建', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    const request = { requestId: 'req-idem', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '只应出现一次' } } }] };
    const first = service.applyTransaction(actor, request);
    const second = service.applyTransaction(actor, request);
    expect(first.status).toBe('applied');
    expect(second.status).toBe('applied');
    expect(second.replayed).toBe(true);
    expect(second.revision).toBe(first.revision);
    expect(loadProject(projectPaths(root, projectId)).content.scenes[0].events).toHaveLength(1);
  });

  it('同 requestId 不同内容被拒绝', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    service.applyTransaction(actor, { requestId: 'req-x', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: 'A' } } }] });
    expect(() => service.applyTransaction(actor, { requestId: 'req-x', projectId, baseRevision: '1', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: 'B' } } }] }))
      .toThrowError(/已用于不同请求内容/);
    try {
      service.applyTransaction(actor, { requestId: 'req-x', projectId, baseRevision: '1', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: 'B' } } }] });
    } catch (error) {
      expect((error as StudioError).code).toBe('REQUEST_ID_CONFLICT');
    }
  });

  it('事务中一项非法则整体不提交', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    const result = service.applyTransaction(actor, {
      requestId: 'req-atomic',
      projectId,
      baseRevision: '0',
      operations: [
        { action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '合法的一句' } } },
        { action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '' } } },
      ],
    });
    expect(result.status).toBe('rejected');
    expect(result.issues.length).toBeGreaterThan(0);
    expect(loadProject(projectPaths(root, projectId)).content.scenes[0].events).toHaveLength(0);
    expect(service.snapshot(projectId).revision).toBe('0');
  });

  it('越权会话不能写入', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    expect(() => service.applyTransaction(READONLY, { requestId: 'req-ro', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: 'x' } } }] }))
      .toThrowError(/作用域/);
  });

  it('暂停会话在提交点被拒绝', () => {
    const projectId = newProject();
    const gated = new EditService({ workspaceRoot: root, importRoots: [path.join(root, 'imports')], sessionGuard: () => false });
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    try {
      gated.applyTransaction(actor, { requestId: 'req-paused', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: 'x' } } }] });
      throw new Error('应当抛出 SESSION_PAUSED');
    } catch (error) {
      expect((error as StudioError).code).toBe('SESSION_PAUSED');
    }
    expect(service.snapshot(projectId).revision).toBe('0');
  });

  it('对象级期望版本可以阻止覆盖后续修改', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    const inserted = service.applyTransaction(actor, { requestId: 'req-obj-1', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '初稿' } } }] });
    const eventId = inserted.changedIds.find((id) => id.startsWith('ev_'))!;
    service.applyTransaction(actor, { requestId: 'req-obj-2', projectId, baseRevision: '1', operations: [{ action: 'event.update', targetIds: [eventId], payload: { sceneId, eventId, params: { text: '人工改过' } } }] });
    const stale = service.applyTransaction(actor, {
      requestId: 'req-obj-3',
      projectId,
      baseRevision: '2',
      operations: [{ action: 'event.update', targetIds: [eventId], expectedObjectRevision: '1', payload: { sceneId, eventId, params: { text: '生成结果覆盖' } } }],
    });
    expect(stale.status).toBe('conflict');
    expect(stale.errorCode).toBe('OBJECT_REVISION_CONFLICT');
    expect(loadProject(projectPaths(root, projectId)).content.scenes[0].events[0].params.text).toBe('人工改过');
  });
});

describe('章节、场景与引用完整性', () => {
  it('可以建立两章分支剧情', () => {
    const projectId = newProject();
    const snapshot = service.snapshot(projectId);
    const firstScene = snapshot.content.scenes[0].sceneId;
    const chapter = service.applyTransaction(actor, { requestId: 'req-ch2', projectId, baseRevision: '0', operations: [{ action: 'chapter.create', payload: { title: '第二章' } }] });
    const chapterId = chapter.changedIds.find((id) => id.startsWith('ch_'))!;
    const scene = service.applyTransaction(actor, { requestId: 'req-sc2', projectId, baseRevision: chapter.revision, operations: [{ action: 'scene.create', payload: { chapterId, title: '旧塔内部' } }] });
    const secondScene = scene.changedIds.find((id) => id.startsWith('sc_'))!;
    const withChoice = service.applyTransaction(actor, {
      requestId: 'req-choice',
      projectId,
      baseRevision: scene.revision,
      operations: [{ action: 'event.insert', payload: { sceneId: firstScene, type: 'choice', params: { prompt: '进塔吗？', options: [{ label: '进塔', targetSceneId: secondScene }, { label: '离开', targetEventId: '' }] } } }],
    });
    expect(withChoice.status).toBe('rejected');
    expect(withChoice.issues.some((issue) => issue.code === 'CHOICE_TARGET')).toBe(true);
    const fixed = service.applyTransaction(actor, {
      requestId: 'req-choice-2',
      projectId,
      baseRevision: scene.revision,
      operations: [
        { action: 'event.insert', payload: { sceneId: firstScene, type: 'dialogue', params: { speaker: '璃', text: '要进去吗？' } } },
        { action: 'event.insert', payload: { sceneId: firstScene, type: 'choice', params: { prompt: '进塔吗？', options: [{ label: '进塔', targetSceneId: secondScene }] } } },
      ],
    });
    expect(fixed.status).toBe('applied');
    expect(service.snapshot(projectId).outline).toHaveLength(2);
  });

  it('删除仍被引用的场景会被拒绝', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    const chapter = service.applyTransaction(actor, { requestId: 'req-ch3', projectId, baseRevision: '0', operations: [{ action: 'chapter.create', payload: { title: '第二章' } }] });
    const chapterId = chapter.changedIds.find((id) => id.startsWith('ch_'))!;
    const scene = service.applyTransaction(actor, { requestId: 'req-sc3', projectId, baseRevision: chapter.revision, operations: [{ action: 'scene.create', payload: { chapterId, title: '塔内' } }] });
    const secondScene = scene.changedIds.find((id) => id.startsWith('sc_'))!;
    service.applyTransaction(actor, { requestId: 'req-jump', projectId, baseRevision: scene.revision, operations: [{ action: 'event.insert', payload: { sceneId, type: 'jump', params: { targetSceneId: secondScene } } }] });
    const rejected = service.applyTransaction(actor, { requestId: 'req-del-scene', projectId, baseRevision: '3', operations: [{ action: 'scene.delete', payload: { sceneId: secondScene } }] });
    expect(rejected.status).toBe('rejected');
    expect(rejected.issues.some((issue) => issue.code === 'SCENE_NOT_FOUND' || issue.code === 'MISSING_REF')).toBe(true);
  });
});

describe('撤销', () => {
  it('撤销不覆盖后续无关修改', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    const first = service.applyTransaction(actor, { requestId: 'req-u1', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '要被撤销' } } }] });
    service.applyTransaction(actor, { requestId: 'req-u2', projectId, baseRevision: first.revision, operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '别人的修改' } } }] });
    const undo = service.undo(actor, { requestId: 'req-undo-1', projectId, undoId: first.undoId!, baseRevision: '2' });
    expect(undo.status).toBe('applied');
    const events = loadProject(projectPaths(root, projectId)).content.scenes[0].events;
    expect(events).toHaveLength(1);
    expect(events[0].params.text).toBe('别人的修改');
  });

  it('后续修改了同一对象时返回依赖冲突', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    const first = service.applyTransaction(actor, { requestId: 'req-d1', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '初稿' } } }] });
    const eventId = first.changedIds.find((id) => id.startsWith('ev_'))!;
    service.applyTransaction(actor, { requestId: 'req-d2', projectId, baseRevision: '1', operations: [{ action: 'event.update', payload: { sceneId, eventId, params: { text: '人工修改' } } }] });
    const undo = service.undo(actor, { requestId: 'req-undo-2', projectId, undoId: first.undoId!, baseRevision: '2' });
    expect(undo.status).toBe('rejected');
    expect(undo.errorCode).toBe('UNDO_DEPENDENCY_CONFLICT');
    expect(undo.issues[0].objectId).toBe(eventId);
    expect(loadProject(projectPaths(root, projectId)).content.scenes[0].events[0].params.text).toBe('人工修改');
  });

  it('重复撤销同一事务被拒绝', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    const first = service.applyTransaction(actor, { requestId: 'req-r1', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: 'A' } } }] });
    expect(service.undo(actor, { requestId: 'req-undo-3', projectId, undoId: first.undoId!, baseRevision: '1' }).status).toBe('applied');
    const again = service.undo(actor, { requestId: 'req-undo-4', projectId, undoId: first.undoId!, baseRevision: '2' });
    expect(again.status).toBe('rejected');
    expect(again.errorCode).toBe('CONFLICT');
  });
});

describe('素材导入与绑定', () => {
  function pngFile(name: string): string {
    const file = path.join(root, 'imports', name);
    writeFile(file, encodeSolidPng(4, 3, [10, 20, 30]));
    return file;
  }

  it('从授权目录导入图片并绑定到背景事件', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    const imported = service.applyTransaction(actor, { requestId: 'req-asset', projectId, baseRevision: '0', operations: [{ action: 'asset.registerFromFile', payload: { sourcePath: pngFile('rain.png'), name: '雨夜背景', kind: 'background' } }] });
    expect(imported.status).toBe('applied');
    const assetId = imported.changedIds.find((id) => id.startsWith('as_'))!;
    const snapshot = service.snapshot(projectId);
    expect(snapshot.content.assets[0].bytes).toBeGreaterThan(0);
    const withBackground = service.applyTransaction(actor, { requestId: 'req-bg', projectId, baseRevision: imported.revision, operations: [{ action: 'event.insert', payload: { sceneId, type: 'background', params: { assetId } } }] });
    expect(withBackground.status).toBe('applied');
  });

  it('未授权路径被拒绝且不写入素材', () => {
    const projectId = newProject();
    const outside = mkdtempSync(path.join(os.tmpdir(), 'studio-outside-'));
    const file = path.join(outside, 'secret.png');
    writeFileSync(file, encodeSolidPng(2, 2, [1, 2, 3]));
    try {
      service.applyTransaction(actor, { requestId: 'req-asset-x', projectId, baseRevision: '0', operations: [{ action: 'asset.registerFromFile', payload: { sourcePath: file, name: '越权', kind: 'background' } }] });
      throw new Error('应当拒绝越权路径');
    } catch (error) {
      expect((error as StudioError).code).toBe('FORBIDDEN');
    } finally {
      rmSync(outside, { recursive: true, force: true });
    }
    expect(service.snapshot(projectId).content.assets).toHaveLength(0);
  });

  it('无法解码的文件不会登记为素材', () => {
    const projectId = newProject();
    const file = path.join(root, 'imports', 'broken.png');
    writeFileSync(file, Buffer.from('not an image at all'));
    const result = service.applyTransaction(actor, { requestId: 'req-asset-bad', projectId, baseRevision: '0', operations: [{ action: 'asset.registerFromFile', payload: { sourcePath: file, name: '坏图', kind: 'background' } }] });
    expect(result.status).toBe('rejected');
    expect(service.snapshot(projectId).content.assets).toHaveLength(0);
  });

  it('素材被引用时不能解除登记', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    const imported = service.applyTransaction(actor, { requestId: 'req-a1', projectId, baseRevision: '0', operations: [{ action: 'asset.registerFromFile', payload: { sourcePath: pngFile('bg2.png'), name: '背景', kind: 'background' } }] });
    const assetId = imported.changedIds.find((id) => id.startsWith('as_'))!;
    service.applyTransaction(actor, { requestId: 'req-a2', projectId, baseRevision: imported.revision, operations: [{ action: 'event.insert', payload: { sceneId, type: 'background', params: { assetId } } }] });
    const rejected = service.applyTransaction(actor, { requestId: 'req-a3', projectId, baseRevision: '2', operations: [{ action: 'asset.delete', payload: { assetId } }] });
    expect(rejected.status).toBe('rejected');
    expect(rejected.issues.some((issue) => issue.code === 'ASSET_IN_USE')).toBe(true);
  });
});

describe('变更流、历史与检查点', () => {
  it('变更序号递增且可以增量查询', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    service.applyTransaction(actor, { requestId: 'req-c1', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: 'A' } } }] });
    service.applyTransaction(actor, { requestId: 'req-c2', projectId, baseRevision: '1', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: 'B' } } }] });
    const feed = service.changes(projectId, 1);
    expect(feed.sequence).toBe(2);
    expect(feed.changes).toHaveLength(1);
    expect(feed.gap).toBe(false);
    expect(feed.changes[0].actor).toBe(actor.actorId);
  });

  it('检查点返回已落盘的修订与内容指纹', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    service.applyTransaction(actor, { requestId: 'req-cp', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: 'A' } } }] });
    const checkpoint = service.checkpoint(projectId);
    expect(checkpoint.revision).toBe('1');
    expect(checkpoint.persistedRevision).toBe('1');
    expect(checkpoint.verifiedFromDisk).toBe(true);
    expect(checkpoint.fileCount).toBeGreaterThan(0);
    expect(checkpoint.contentDigest).toMatch(/^[0-9a-f]{64}$/);
  });

  it('历史记录包含操作与补偿操作', () => {
    const projectId = newProject();
    const sceneId = service.snapshot(projectId).content.scenes[0].sceneId;
    service.applyTransaction(actor, { requestId: 'req-h1', projectId, baseRevision: '0', operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: 'A' } } }] });
    const history = readHistory(projectPaths(root, projectId));
    const edit = history.find((record) => record.sequence === 1)!;
    expect(edit.operations[0].action).toBe('event.insert');
    expect(edit.inverseOperations[0].action).toBe('event.delete');
    expect(edit.actor).toBe(actor.actorId);
  });
});
