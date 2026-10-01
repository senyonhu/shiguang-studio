import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mkdtempSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { MediaJobService } from '../../../server/src/media/jobs.ts';
import { FixtureMediaAdapter } from '../../../server/src/media/adapters/fixture.ts';
import type { MediaRequest } from '../../../server/src/media/types.ts';
import { createProject } from '../../../server/src/store/workspace.ts';

let workspace: string;
let projectId: string;
beforeEach(() => {
  workspace = mkdtempSync(path.join(os.tmpdir(), 'studio-media-guard-'));
  projectId = createProject(workspace, { title: '暂停竞态' }).projectId;
});
afterEach(() => { vi.restoreAllMocks(); rmSync(workspace, { recursive: true, force: true }); });
const actor = { actorId: 'ai:guard', sessionId: 'sess_guard0001' };
const request = (): MediaRequest => ({ apiVersion: '1', requestId: 'guard-job', projectId, providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text', prompt: '暂停', references: [], output: { count: 1 } });

describe('生成任务会话暂停守卫（KI-036）', () => {
  it.each(['before', 'validate', 'estimate'] as const)('在 %s 阶段暂停会话，不创建任务或预留预算', async (phase) => {
    let writable = phase !== 'before';
    if (phase === 'validate') {
      const original = FixtureMediaAdapter.prototype.validate;
      vi.spyOn(FixtureMediaAdapter.prototype, 'validate').mockImplementation(async function (this: FixtureMediaAdapter, input) {
        const result = await original.call(this, input);
        writable = false;
        return result;
      });
    } else if (phase === 'estimate') {
      const original = FixtureMediaAdapter.prototype.estimate;
      vi.spyOn(FixtureMediaAdapter.prototype, 'estimate').mockImplementation(async function (this: FixtureMediaAdapter, input) {
        const result = await original.call(this, input);
        writable = false;
        return result;
      });
    }
    const jobs = new MediaJobService({ workspaceRoot: workspace, sessionGuard: () => writable });
    await expect(jobs.createJob(actor, request())).rejects.toMatchObject({ code: 'SESSION_PAUSED' });
    expect(jobs.listJobs(projectId)).toHaveLength(0);
    expect(jobs.readBudget(projectId).reserved).toBe(0);
  });

  it('已存在任务在暂停期间仍可幂等查询，新请求被拒绝，恢复后可创建', async () => {
    let writable = true;
    const jobs = new MediaJobService({ workspaceRoot: workspace, sessionGuard: () => writable });
    const first = await jobs.createJob(actor, request());
    const reserved = jobs.readBudget(projectId).reserved;
    writable = false;
    const replay = await jobs.createJob(actor, request());
    expect(replay.replayed).toBe(true);
    expect(replay.job.jobId).toBe(first.job.jobId);
    await expect(jobs.createJob(actor, { ...request(), requestId: 'new-job' })).rejects.toMatchObject({ code: 'SESSION_PAUSED' });
    expect(jobs.readBudget(projectId).reserved).toBe(reserved);
    writable = true;
    await jobs.createJob(actor, { ...request(), requestId: 'resumed-job' });
    expect(jobs.listJobs(projectId)).toHaveLength(2);
  });
});
