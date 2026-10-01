import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { rmSync } from 'node:fs';
import { api, delay, makeWorkspace, pairSession, readPairingKey, startBackend, trace } from '../helpers/harness.ts';
import type { ServiceHandle } from '../helpers/harness.ts';
import type { MediaJobSummary } from '../../../server/src/media/types.ts';
let workspace: string;
let service: ServiceHandle;
let token: string;
let sessionId: string;
beforeAll(async () => {
  workspace = makeWorkspace('review-media');
  service = await startBackend(workspace, { name: 'review-media' });
  const session = await pairSession(service.url, readPairingKey(workspace), 'review-media');
  token = session.token;
  sessionId = session.sessionId;
});
afterAll(async () => { await service?.stop(); if (workspace) rmSync(workspace, { recursive: true, force: true }); });
const request = (requestId: string, count = 1, mode = 'ok') => ({ requestId, providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text', prompt: requestId, output: { count, width: 16, height: 9 }, providerOptions: { fixtureMode: mode } });
async function project() {
  const response = await api(service.url, token, 'POST', '/v1/projects', { title: '独立复核回归' });
  expect(response.status).toBe(201);
  return (response.body as { projectId: string }).projectId;
}
async function waitFor(jobId: string, status: string) {
  for (let attempt = 0; attempt < 80; attempt++) {
    const response = await api(service.url, token, 'GET', `/v1/jobs/${jobId}`);
    const job = response.body as MediaJobSummary;
    if (job.status === status) return job;
    await delay(100);
  }
  throw new Error(`任务 ${jobId} 没有达到 ${status}`);
}

describe('独立复核：生成任务行为回归', () => {
  it('同一任务的两个候选分别入库，重复接受同一候选保持幂等（KI-037）', async () => {
    const projectId = await project();
    const response = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs`, request('multi-output', 2));
    expect(response.status).toBe(201);
    const job = await waitFor((response.body as MediaJobSummary).jobId, 'available');
    const assetIds: string[] = [];
    for (const output of job.outputs) {
      const body = { outputId: output.outputId };
      const accepted = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs/${job.jobId}/accept`, body);
      expect(accepted.status, JSON.stringify(accepted.body)).toBe(200);
      const result = accepted.body as { assetId: string; revision: string };
      assetIds.push(result.assetId);
      const replay = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs/${job.jobId}/accept`, body);
      expect(replay.status, JSON.stringify(replay.body)).toBe(200);
      expect((replay.body as { assetId: string; revision: string }).assetId).toBe(result.assetId);
      expect((replay.body as { revision: string }).revision).toBe(result.revision);
    }
    expect(new Set(assetIds).size).toBe(2);
    const assets = await api(service.url, token, 'GET', `/v1/projects/${projectId}/assets`);
    expect((assets.body as { assets: { assetId: string }[] }).assets.map((asset) => asset.assetId).sort()).toEqual(assetIds.sort());
    trace('review-multi-output', { jobId: job.jobId, assetIds });
  });

  it('unknown 不挡住正常任务，未知任务不重复提交且保留预算（KI-038）', async () => {
    const projectId = await project();
    const slow = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs`, request('slow-before-unknown', 1, 'slow'));
    const unknown = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs`, request('newer-unknown', 1, 'poll-unknown'));
    expect(slow.status).toBe(201);
    expect(unknown.status).toBe(201);
    const unknownJob = await waitFor((unknown.body as MediaJobSummary).jobId, 'unknown');
    // 在 unknown 已占据队列记录后，再提交一个任务，验证调度槽确实已释放。
    const next = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs`, request('after-unknown', 1, 'slow'));
    expect(next.status).toBe(201);
    const nextJob = await waitFor((next.body as MediaJobSummary).jobId, 'available');
    expect((await waitFor((slow.body as MediaJobSummary).jobId, 'available')).attempts).toBe(1);
    const unchanged = await waitFor(unknownJob.jobId, 'unknown');
    expect(unchanged.attempts).toBe(1);
    expect(unchanged.providerJobId).toBe(unknownJob.providerJobId);
    const budget = (await api(service.url, token, 'GET', `/v1/projects/${projectId}/media-jobs`)).body as { budget: { reserved: number } };
    expect(budget.budget.reserved).toBeGreaterThanOrEqual(unknownJob.estimate.estimated ?? 0);
    const cancelled = await api(service.url, token, 'POST', `/v1/jobs/${unknownJob.jobId}/cancel`, {});
    expect(cancelled.status).toBe(200);
    await waitFor(unknownJob.jobId, 'cancelled');
    trace('review-unknown-queue', { unknownJobId: unknownJob.jobId, nextJobId: nextJob.jobId, attempts: unchanged.attempts, reserved: budget.budget.reserved });
  });

  it('人工暂停后新生成返回 423，任务与预算不变，恢复后成功（KI-036）', async () => {
    const projectId = await project();
    const humanResponse = await fetch(`${service.url}/v1/ui-session`, { method: 'POST', headers: { origin: 'http://127.0.0.1:5173' } });
    expect(humanResponse.status).toBe(201);
    const human = await humanResponse.json() as { token: string };
    const paused = await api(service.url, human.token, 'POST', `/v1/ai-sessions/${sessionId}/pause`, {});
    expect(paused.status).toBe(200);
    const before = (await api(service.url, token, 'GET', `/v1/projects/${projectId}/media-jobs`)).body as { jobs: unknown[]; budget: { reserved: number; spent: number } };
    const blocked = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs`, request('paused-job'));
    expect(blocked.status).toBe(423);
    expect((blocked.body as { error: { code: string } }).error.code).toBe('SESSION_PAUSED');
    const after = (await api(service.url, token, 'GET', `/v1/projects/${projectId}/media-jobs`)).body as typeof before;
    expect(after.jobs).toEqual(before.jobs);
    expect(after.budget.reserved).toBe(before.budget.reserved);
    expect(after.budget.spent).toBe(before.budget.spent);
    const resumed = await api(service.url, human.token, 'POST', `/v1/ai-sessions/${sessionId}/resume`, {});
    expect(resumed.status).toBe(200);
    const created = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs`, request('paused-job'));
    expect(created.status).toBe(201);
    trace('review-pause-media', { blockedHttp: blocked.status, resumedHttp: created.status });
  });
});
