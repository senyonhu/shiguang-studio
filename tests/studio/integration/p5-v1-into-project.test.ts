import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { api, makeWorkspace, pairSession, readPairingKey, startBackend, trace, REPO_ROOT } from '../helpers/harness.ts';
import type { ServiceHandle } from '../helpers/harness.ts';

// KI-011：v1 迁移支持导入“既有工程”（--project）。
// 用例独占一份工作区与后台，避免迁移脚本的会话配对影响其它用例。
// 显式工作区现在默认保留；--keep-workspace 只影响失败时脚本自建的临时目录。
const run = promisify(execFile);

let workspace = '';
let service: ServiceHandle;
let token = '';

beforeAll(async () => {
  workspace = makeWorkspace('p5-v1-into');
  service = await startBackend(workspace, { name: 'p5-v1-into' });
  const session = await pairSession(service.url, readPairingKey(workspace), 'p5-v1-into');
  token = session.token;
}, 120000);

afterAll(async () => {
  await service?.stop();
  if (workspace) rmSync(workspace, { recursive: true, force: true });
});

describe('P5 v1 迁移：导入到已有工程（KI-011）', () => {
  it('--project 把 v1 节点写进目标工程的新场景，并与新建路径共用同一套映射', async () => {
    const created = await api(service.url, token, 'POST', '/v1/projects', { title: '既有工程', chapterTitle: '第一章', sceneTitle: '开场' });
    expect(created.status, `POST /v1/projects: ${JSON.stringify(created.body).slice(0, 220)}`).toBe(201);
    const projectId = (created.body as { projectId?: string } | null)?.projectId ?? '';
    expect(projectId, 'POST /v1/projects 没有返回 projectId').toBeTruthy();

    const before = await api(service.url, token, 'GET', `/v1/projects/${projectId}/outline`);
    expect(before.status, `GET outline: ${JSON.stringify(before.body).slice(0, 220)}`).toBe(200);
    const beforeOutline = (before.body as { outline?: { scenes: unknown[] }[] } | null)?.outline ?? [];
    expect(beforeOutline.length).toBeGreaterThan(0);
    const scenesBefore = beforeOutline[0].scenes.length;

    // 最小 v1 工程；fixture 和报告由测试独立管理。
    const fixturePath = path.join(mkdtempSync(path.join(os.tmpdir(), 'studio-v1-')), 'legacy.json');
    writeFileSync(fixturePath, JSON.stringify({
      version: 1,
      id: 'legacy_into',
      title: '导入到既有工程',
      chapter: '旧章',
      entry: 'n1',
      assets: [],
      nodes: [
        { id: 'n1', title: '开场', type: 'dialogue', speaker: '璃', text: '来自 v1 的对白。', background: '', character: '', next: 'n2' },
        { id: 'n2', title: '结束', type: 'ending', speaker: '', text: '', background: '', character: '' },
      ],
    }, null, 2), 'utf8');
    const reportPath = path.join(path.dirname(fixturePath), 'report.json');

    const result = await run(process.execPath, [
      path.join(REPO_ROOT, 'scripts', 'studio-migrate.mjs'),
      '--input', fixturePath,
      '--project', projectId,
      '--workspace', workspace,
      '--keep-workspace',
      '--json', reportPath,
    ], { cwd: REPO_ROOT });
    expect(result.stderr ?? '', result.stderr ?? '').not.toContain('Error');

    const report = JSON.parse(readFileSync(reportPath, 'utf8')) as {
      createdProjectId: string;
      importedIntoExistingProject?: boolean;
      appliedOperations: number;
      validation: { ok: boolean };
      warnings: string[];
    };
    expect(report.createdProjectId).toBe(projectId);
    expect(report.importedIntoExistingProject).toBe(true);
    expect(report.validation.ok).toBe(true);
    expect(report.appliedOperations).toBeGreaterThan(0);

    // 脚本自己配对过会话（channel=mcp），可能顶掉先前会话 → 重新配对再读
    const sessionAfter = await pairSession(service.url, readPairingKey(workspace), 'p5-v1-into-after');
    const after = await api(service.url, sessionAfter.token, 'GET', `/v1/projects/${projectId}/outline`);
    expect(after.status, `GET outline(after): ${JSON.stringify(after.body).slice(0, 220)}`).toBe(200);
    const scenesAfter = ((after.body as { outline?: { scenes: { sceneId: string; title: string }[] }[] } | null)?.outline ?? [])[0]?.scenes ?? [];
    expect(scenesAfter.length).toBe(scenesBefore + 1);
    expect(scenesAfter.some((scene) => scene.title.includes('v1 导入'))).toBe(true);
    trace('ki-011-migrate-into-project', { projectId, operations: report.appliedOperations, scenesBefore, scenesAfter: scenesAfter.length, warnings: report.warnings.length });
  }, 240000);
});
