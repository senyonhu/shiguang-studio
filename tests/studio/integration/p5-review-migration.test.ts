import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { encodeSolidPng } from '../../../server/src/media/decode.ts';
import { loadProject } from '../../../server/src/store/doc.ts';
import { projectPaths } from '../../../server/src/store/paths.ts';
import { applyRuntimeAction, startRuntime } from '../../../src/runtime/kernel.ts';
import { api, pairSession, readPairingKey, startBackend, trace, REPO_ROOT } from '../helpers/harness.ts';
import type { ServiceHandle } from '../helpers/harness.ts';
import type { Project, StoryNode } from '../../../src/studio/model.ts';
import { inspectProject } from '../../../src/studio/model.ts';
const run = promisify(execFile);
let workspace: string;
let service: ServiceHandle | undefined;
beforeEach(() => { workspace = mkdtempSync(path.join(os.tmpdir(), 'studio-migrate-regression-')); });
afterEach(async () => { await service?.stop(); service = undefined; rmSync(workspace, { recursive: true, force: true }); });
function fixture(): Project {
  const png = (color: [number, number, number]) => `data:image/png;base64,${encodeSolidPng(4, 3, color).toString('base64')}`;
  const node = (id: string, type: StoryNode['type'], extras: Partial<StoryNode>): StoryNode => ({ id, type, title: id, speaker: '', text: '', background: 'bg1', character: '', ...extras });
  return {
    version: 1, id: 'legacy_review', title: '剧情图回归', chapter: '旧章', entry: 'start',
    assets: [{ id: 'bg1', name: '背景一', kind: 'background', src: png([20, 30, 40]) }, { id: 'bg2', name: '背景二', kind: 'background', src: png([40, 50, 60]) }, { id: 'ch1', name: '人物', kind: 'character', src: png([60, 70, 80]) }],
    nodes: [
      node('unused', 'ending', { title: '不应从这里开始' }),
      node('finish-a', 'ending', { title: '结局 A', background: 'bg2' }),
      node('start', 'dialogue', { text: '正确入口', character: 'ch1', next: 'pick' }),
      node('branch-a', 'dialogue', { text: '分支 A', background: 'bg2', next: 'level-skip' }),
      node('pick', 'choice', { choices: [{ label: 'A', next: 'branch-a' }, { label: 'B', next: 'branch_a' }] }),
      node('level-skip', 'level', { level: 'constellation', next: 'finish-a' }),
      node('branch_a', 'ending', { title: '结局 B' }),
    ],
  };
}
async function migrate(source: unknown, extra: string[] = [], explicit = true) {
  const input = path.join(workspace, 'legacy.json');
  const reportFile = path.join(workspace, 'migration-report.json');
  writeFileSync(input, JSON.stringify(source));
  const args = [path.join(REPO_ROOT, 'scripts', 'studio-migrate.mjs'), '--input', input, '--json', reportFile, ...(explicit ? ['--workspace', workspace] : []), ...extra];
  const result = await run(process.execPath, args, { cwd: REPO_ROOT });
  const report = JSON.parse(readFileSync(reportFile, 'utf8')) as { workspace: string; createdProjectId: string; sourcePreservedAt: string; validation: { ok: boolean }; warnings: string[]; appliedOperations: number };
  return { result, report, input };
}

describe('独立复核：v1 迁移回归', () => {
  it('显式工作区成功迁移后保留已有数据，入口、双分支、next 和舞台状态保持（KI-034/035）', async () => {
    const sentinel = path.join(workspace, 'existing-data.txt');
    writeFileSync(sentinel, '保留已有工程数据');
    const source = fixture();
    expect(inspectProject(source)).toEqual([]);
    const { report, input } = await migrate(source);
    expect(report.validation.ok).toBe(true);
    expect(readFileSync(sentinel, 'utf8')).toBe('保留已有工程数据');
    expect(readFileSync(path.join(workspace, report.sourcePreservedAt), 'utf8')).toBe(readFileSync(input, 'utf8'));
    const content = loadProject(projectPaths(workspace, report.createdProjectId)).content;
    const start = startRuntime(content, content.project.entrySceneId);
    expect(start.status).toBe('awaitingAdvance');
    expect(start.lines.at(-1)?.text).toBe('正确入口');
    const choice = applyRuntimeAction(content, start, { type: 'advance' });
    expect(choice.status).toBe('awaitingChoice');
    const branchA = applyRuntimeAction(content, choice, { type: 'choose', option: 0 });
    expect(branchA.lines.at(-1)?.text).toBe('分支 A');
    expect(branchA.stage.background).toBe(content.assets.find((asset) => asset.name === '背景二')?.assetId);
    expect(branchA.stage.characters).toEqual([]);
    const finishA = applyRuntimeAction(content, branchA, { type: 'advance' });
    expect(finishA.status).toBe('ended');
    expect(finishA.ended?.title).toBe('结局 A');
    const finishB = applyRuntimeAction(content, choice, { type: 'choose', option: 1 });
    expect(finishB.status).toBe('ended');
    expect(finishB.ended?.title).toBe('结局 B');
    expect(report.warnings.join(' ')).toContain('关卡节点');
    trace('review-migration-graph', { projectId: report.createdProjectId, start: start.lines.at(-1)?.text, finishA: finishA.ended?.title, finishB: finishB.ended?.title, operations: report.appliedOperations });
  });

  it('实际导入失败也保留显式工作区与已有文件（KI-034）', async () => {
    const sentinel = path.join(workspace, 'existing-data.txt');
    writeFileSync(sentinel, '失败也必须保留');
    const source = fixture();
    source.assets[0].src = 'data:image/png;base64,bm90YW5pbWFnZQ==';
    await expect(migrate(source)).rejects.toMatchObject({ code: 1 });
    expect(readFileSync(sentinel, 'utf8')).toBe('失败也必须保留');
    expect(existsSync(path.join(workspace, 'legacy.json'))).toBe(true);
    trace('review-migration-preserve-failure', { preserved: true });
  });

  it('跨 200 操作的前向引用有效，重复导入既有工程不冲突且不关闭已有后台（KI-035）', async () => {
    const source = fixture();
    source.nodes = Array.from({ length: 110 }, (_, index) => ({ id: `n${index}`, type: 'dialogue' as const, title: '', speaker: '', text: `段落 ${index}`, background: 'bg1', character: '', next: index === 109 ? 'finish' : `n${index + 1}` }));
    source.nodes.unshift({ id: 'pick', type: 'choice', title: '跨批选择', speaker: '', text: '', background: 'bg1', character: '', choices: [{ label: '末尾段落', next: 'n109' }] });
    source.nodes.push({ id: 'finish', type: 'ending', title: '跨批结局', speaker: '', text: '', background: 'bg2', character: '' });
    source.entry = 'pick';
    expect(inspectProject(source)).toEqual([]);
    service = await startBackend(workspace, { name: 'review-migration-existing' });
    const session = await pairSession(service.url, readPairingKey(workspace), 'existing-migration');
    const created = await api(service.url, session.token, 'POST', '/v1/projects', { title: '既有工程' });
    const projectId = (created.body as { projectId: string }).projectId;
    for (let iteration = 0; iteration < 2; iteration++) {
      const { report } = await migrate(source, ['--project', projectId]);
      expect(report.validation.ok).toBe(true);
      expect(report.appliedOperations).toBeGreaterThan(200);
      expect((await fetch(`${service.url}/v1/health`)).ok).toBe(true);
      const content = loadProject(projectPaths(workspace, projectId)).content;
      const imported = content.scenes.filter((scene) => scene.title === 'v1 导入').at(-1)!;
      const entry = startRuntime(content, imported.sceneId);
      expect(entry.status).toBe('awaitingChoice');
      const last = applyRuntimeAction(content, entry, { type: 'choose', option: 0 });
      expect(last.lines.at(-1)?.text).toBe('段落 109');
      expect(applyRuntimeAction(content, last, { type: 'advance' }).ended?.title).toBe('跨批结局');
    }
    const content = loadProject(projectPaths(workspace, projectId)).content;
    expect(content.scenes).toHaveLength(3);
    expect(new Set(content.assets.map((asset) => asset.assetId)).size).toBe(6);
    trace('review-migration-cross-batch', { projectId, scenes: content.scenes.length, assets: content.assets.length });
  }, 120000);

  it('未指定 workspace 时成功结果仍保留，并在报告中给出输出目录（KI-034）', async () => {
    const { report } = await migrate(fixture(), [], false);
    try {
      expect(existsSync(path.join(report.workspace, report.createdProjectId, 'project.json'))).toBe(true);
      expect(report.validation.ok).toBe(true);
      trace('review-migration-default-output', { preserved: true, projectId: report.createdProjectId });
    } finally {
      // 只清理报告指向、且属于脚本自建临时目录的本次测试输出。
      expect(path.dirname(report.workspace)).toBe(path.resolve(os.tmpdir()));
      expect(path.basename(report.workspace)).toMatch(/^studio-migrate-/);
      rmSync(report.workspace, { recursive: true, force: true });
    }
  });
});
