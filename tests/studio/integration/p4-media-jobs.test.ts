// P4 集成测试：图片/视频生成协议链路（明确标注的测试适配器 fixture）。
// 覆盖：能力声明、参数校验失败、预算、幂等、排队→生成→下载→校验→候选可用、
//       提交结果未知不重复提交、查询状态未知、取消、重启恢复、下载失败、绑定冲突不覆盖。
// 注意：全部用例使用 fixture 适配器（mode=fixture_provider），没有任何真实服务调用。
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { McpClient, REPO_ROOT, api, delay, makeWorkspace, pairSession, readPairingKey, startBackend, trace } from '../helpers/harness.ts';

const PLUGIN_BUNDLE = path.join(REPO_ROOT, 'plugin', 'studio-author', 'server', 'mcp-stdio.mjs');
import type { ServiceHandle } from '../helpers/harness.ts';

let workspace = '';
let service: ServiceHandle;
let token = '';
let projectId = '';
let sceneId = '';
let eventId = '';
let revision = '';

interface JobBody {
  jobId: string;
  status: string;
  stage: string;
  outputs: { outputId: string; path: string; sha256: string; playable: boolean; registeredAssetId?: string; bindingIssue?: string; boundAt?: string }[];
  errors: { code: string; message: string }[];
  notes: string[];
  estimate: { state: string; estimated?: number; currency: string };
  actualCost?: { state: string };
  providerIsTestAdapter: boolean;
  attempts: number;
  providerJobId: string | null;
}

async function createJob(body: Record<string, unknown>): Promise<{ status: number; body: JobBody & { replayed?: boolean; error?: { code: string; message: string; issues?: { code: string }[] } } }> {
  const response = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs`, body);
  return response as never;
}

/** 轮询任务直到进入终态（后台定时器推进状态机）。 */
async function waitForJob(jobId: string, wanted: string[], timeoutMs = 20000): Promise<JobBody> {
  const deadline = Date.now() + timeoutMs;
  let last: JobBody | null = null;
  while (Date.now() < deadline) {
    const response = await api(service.url, token, 'GET', `/v1/jobs/${jobId}`);
    last = response.body as unknown as JobBody;
    if (wanted.includes(last.status)) return last;
    await delay(120);
  }
  throw new Error(`任务 ${jobId} 未在 ${timeoutMs}ms 内进入 ${wanted.join('/')}；最后状态 ${last?.status}/${last?.stage}`);
}

beforeAll(async () => {
  workspace = makeWorkspace('p4-media');
  service = await startBackend(workspace, { name: 'p4-media' });
  const session = await pairSession(service.url, readPairingKey(workspace), 'p4-media-ai');
  token = session.token;
  const created = await api(service.url, token, 'POST', '/v1/projects', { title: '雨夜旧塔（P4 生成协议）', chapterTitle: '第一章', sceneTitle: '塔前' });
  const createdBody = created.body as { projectId: string; sceneId: string; revision: string };
  projectId = createdBody.projectId;
  sceneId = createdBody.sceneId;
  revision = createdBody.revision;
  const jump = await api(service.url, token, 'POST', `/v1/projects/${projectId}/transactions`, {
    requestId: 'p4-target-event',
    projectId,
    baseRevision: revision,
    operations: [{ action: 'event.insert', payload: { sceneId, type: 'jump', params: { targetSceneId: sceneId }, eventId: 'ev_p4target01' } }],
  });
  expect((jump.body as { status: string }).status, JSON.stringify(jump.body)).toBe('applied');
  revision = (jump.body as { revision: string }).revision;
  eventId = 'ev_p4target01';
});

afterAll(async () => {
  await service?.stop();
});

describe('P4 生成服务能力与请求校验（测试适配器）', () => {
  it('列出生成服务：明确标注为测试适配器且不访问网络', async () => {
    const response = await api(service.url, token, 'GET', '/v1/media/providers');
    expect(response.status).toBe(200);
    const body = response.body as { providers: { providerId: string; adapter: string; isTestAdapter: boolean; models: { modelId: string; kinds: string[] }[]; notes: string[]; supportsCancel: boolean }[]; note: string };
    expect(body.providers).toHaveLength(1);
    const provider = body.providers[0];
    expect(provider.isTestAdapter).toBe(true);
    expect(provider.adapter).toBe('fixture');
    expect(provider.models.map((model) => model.modelId)).toContain('fixture-video-1');
    expect(provider.notes.join(' ')).toContain('不会访问网络');
    expect(body.note).toContain('尚未接入');
    trace('p4-providers', { providers: body.providers.length, modelCount: provider.models.length, note: body.note });
  });

  it('能力校验拒绝不支持的参数（不创建任务）', async () => {
    // 6 在协议范围（1–8）内，但超过测试适配器模型上限 4 → 由适配器能力校验拒绝
    const rejected = await createJob({
      requestId: 'p4-invalid', providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text', prompt: '雨夜旧塔内部', output: { count: 6 },
    });
    expect(rejected.status).toBe(422);
    expect(rejected.body.error?.code).toBe('VALIDATION_FAILED');
    expect(rejected.body.error?.issues?.some((issue) => issue.code === 'COUNT_UNSUPPORTED')).toBe(true);

    // 协议层硬上限
    const outOfRange = await createJob({
      requestId: 'p4-invalid-2', providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text', prompt: 'x', output: { count: 99 },
    });
    expect(outOfRange.status).toBe(400);
    expect(outOfRange.body.error?.code).toBe('BAD_REQUEST');
  });

  it('未接入的服务返回明确错误而不是静默失败', async () => {
    const response = await api(service.url, token, 'POST', '/v1/media/providers/does-not-exist/models', {});
    expect(response.status).toBe(404); // 该路径不存在
    const estimate = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs/estimate`, {
      providerId: 'openai-images', modelId: 'x', kind: 'image.text', prompt: 'x', output: { count: 1 },
    });
    expect(estimate.status).toBe(422);
    expect((estimate.body as { error: { code: string } }).error.code).toBe('UNSUPPORTED_CAPABILITY');
  });

  it('估价返回费用状态与预算情况', async () => {
    const response = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs/estimate`, {
      requestId: 'p4-estimate', providerId: 'fixture-local', modelId: 'fixture-video-1', kind: 'video.text', prompt: '塔内转场', output: { count: 1, durationSeconds: 4 },
    });
    expect(response.status).toBe(200);
    const body = response.body as { estimate: { state: string; currency: string; estimated?: number }; validation: { ok: boolean }; budget: { limit: number; reserved: number; spent: number }; withinBudget: boolean };
    expect(body.validation.ok).toBe(true);
    expect(body.estimate.state).toBe('estimated');
    expect(body.estimate.currency).toBe('fixture-point');
    expect(body.withinBudget).toBe(true);
  });
});

describe('P4 任务状态机与候选产物', () => {
  it('图片任务走完队列→生成→下载→校验，候选可用且可解码', async () => {
    const created = await createJob({
      requestId: 'p4-image-ok',
      providerId: 'fixture-local',
      modelId: 'fixture-image-1',
      kind: 'image.text',
      prompt: '雨夜旧塔内部，暖色提灯照亮石阶',
      output: { count: 2, aspectRatio: '16:9', width: 48, height: 27 },
    });
    expect(created.status).toBe(201);
    const jobId = created.body.jobId;
    const job = await waitForJob(jobId, ['available', 'failed']);
    expect(job.status, JSON.stringify(job.errors)).toBe('available');
    expect(job.outputs).toHaveLength(2);
    expect(job.outputs.every((output) => output.playable)).toBe(true);
    expect(job.providerIsTestAdapter).toBe(true);
    for (const output of job.outputs) {
      const abs = path.join(workspace, projectId, output.path);
      expect(existsSync(abs)).toBe(true);
      const bytes = readFileSync(abs);
      expect(bytes.subarray(0, 8)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
    }
    expect(job.actualCost?.state).toBe('confirmed');
    trace('p4-image-job', { jobId, status: job.status, outputs: job.outputs.map((output) => output.path), notes: job.notes });
  });

  it('视频任务产出明确标注不可播放的占位文件（协议验证用）', async () => {
    const created = await createJob({
      requestId: 'p4-video-fixture',
      providerId: 'fixture-local',
      modelId: 'fixture-video-1',
      kind: 'video.text',
      prompt: '从塔门推入石阶的转场',
      output: { count: 1, durationSeconds: 3, aspectRatio: '16:9' },
    });
    const job = await waitForJob(created.body.jobId, ['available', 'failed']);
    expect(job.status).toBe('available');
    expect(job.outputs).toHaveLength(1);
    expect(job.outputs[0].playable).toBe(false);
    const summary = await api(service.url, token, 'GET', `/v1/jobs/${job.jobId}`);
    expect((summary.body as { notes: string[] }).notes.join(' ')).toContain('候选产物已下载');
    trace('p4-video-job', { jobId: job.jobId, playable: job.outputs[0].playable });
  });

  it('采用候选：登记为素材并绑定剧情，生成物进入素材库', async () => {
    const listed = await api(service.url, token, 'GET', `/v1/projects/${projectId}/media-jobs`);
    const jobs = (listed.body as { jobs: JobBody[] }).jobs;
    const imageJob = jobs.find((job) => job.status === 'available' && job.outputs.some((output) => output.playable));
    expect(imageJob).toBeTruthy();

    const accepted = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs/${imageJob!.jobId}/accept`, {
      outputId: imageJob!.outputs[0].outputId,
      name: '雨夜塔内（候选）',
      kind: 'background',
      binding: { sceneId, eventId, field: 'assetId', expectedObjectRevision: '99' },
    });
    // 对象级期望版本是伪造的 → 事务被拒绝，候选保留
    expect(accepted.status).toBe(409);
    const rejected = accepted.body as { errorCode: string; issues: { code: string }[]; job: JobBody };
    expect(rejected.errorCode).toBe('OBJECT_REVISION_CONFLICT');
    expect(rejected.job.outputs.find((output) => output.outputId === imageJob!.outputs[0].outputId)?.bindingIssue).toBeTruthy();

    // 不带绑定的接受：只登记素材
    const registered = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs/${imageJob!.jobId}/accept`, {
      outputId: imageJob!.outputs[0].outputId,
      name: '雨夜塔内（候选）',
      kind: 'background',
    });
    expect(registered.status).toBe(200);
    const registeredBody = registered.body as { status: string; assetId: string; revision: string; job: JobBody };
    expect(registeredBody.status).toBe('applied');
    expect(registeredBody.assetId).toMatch(/^as_/);
    revision = registeredBody.revision;

    const assets = await api(service.url, token, 'GET', `/v1/projects/${projectId}/assets`);
    const asset = (assets.body as { assets: { assetId: string; kind: string; sha256: string; provenance?: Record<string, unknown> }[] }).assets.find((item) => item.assetId === registeredBody.assetId);
    expect(asset?.kind).toBe('background');
    expect(asset?.provenance?.testFixture).toBe(true);
    expect(asset?.provenance?.jobId).toBe(imageJob!.jobId);
    trace('p4-accept', { jobId: imageJob!.jobId, assetId: registeredBody.assetId, revision, provenance: asset?.provenance });
  });

  it('同 requestId 重复提交返回既有任务，不产生第二个任务', async () => {
    const request = {
      requestId: 'p4-idempotent', providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text', prompt: '同一请求', output: { count: 1 },
    };
    const first = await createJob(request);
    const second = await createJob(request);
    expect(first.body.jobId).toBe(second.body.jobId);
    expect(second.body.replayed).toBe(true);
    const changed = await createJob({ ...request, prompt: '同一 requestId 但内容不同' });
    expect(changed.status).toBe(409);
    expect(changed.body.error?.code).toBe('REQUEST_ID_CONFLICT');
  });
});

describe('P4 失败路径与恢复', () => {
  it('提交结果未知：标记 unknown 且不重复提交', async () => {
    const created = await createJob({
      requestId: 'p4-submit-unknown', providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text',
      prompt: '提交超时', output: { count: 1 }, providerOptions: { fixtureMode: 'submit-timeout' },
    });
    const job = await waitForJob(created.body.jobId, ['unknown', 'failed', 'available']);
    expect(job.status).toBe('unknown');
    expect(job.errors.some((error) => error.code === 'SUBMIT_RESULT_UNKNOWN')).toBe(true);
    expect(job.providerJobId).toBeNull();
    expect(job.notes.join(' ')).toContain('不自动重复提交');
    await delay(600);
    const again = await api(service.url, token, 'GET', `/v1/jobs/${job.jobId}`);
    expect((again.body as JobBody).attempts).toBe(1); // 没有自动重试提交
    trace('p4-submit-unknown', { jobId: job.jobId, attempts: (again.body as JobBody).attempts });
  });

  it('查询状态未知：标记 unknown 并给出可重试错误，不伪造成功', async () => {
    const created = await createJob({
      requestId: 'p4-poll-unknown', providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text',
      prompt: '状态未知', output: { count: 1 }, providerOptions: { fixtureMode: 'poll-unknown' },
    });
    const job = await waitForJob(created.body.jobId, ['unknown', 'failed', 'available']);
    expect(job.status).toBe('unknown');
    expect(job.errors.some((error) => error.code === 'PROVIDER_STATUS_UNKNOWN')).toBe(true);
  });

  it('服务拒绝：任务失败并释放预算预留', async () => {
    const before = await api(service.url, token, 'GET', `/v1/projects/${projectId}/media-jobs`);
    const reservedBefore = (before.body as { budget: { reserved: number } }).budget.reserved;
    const created = await createJob({
      requestId: 'p4-provider-error', providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text',
      prompt: '服务拒绝', output: { count: 1 }, providerOptions: { fixtureMode: 'provider-error' },
    });
    const job = await waitForJob(created.body.jobId, ['failed', 'available', 'unknown']);
    expect(job.status).toBe('failed');
    expect(job.errors.some((error) => error.code === 'PROVIDER_REJECTED')).toBe(true);
    const after = await api(service.url, token, 'GET', `/v1/projects/${projectId}/media-jobs`);
    expect((after.body as { budget: { reserved: number } }).budget.reserved).toBeLessThanOrEqual(reservedBefore);
  });

  it('下载失败：任务失败但不产生半截候选', async () => {
    const created = await createJob({
      requestId: 'p4-download-failure', providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text',
      prompt: '下载失败', output: { count: 1 }, providerOptions: { fixtureMode: 'download-failure' },
    });
    const job = await waitForJob(created.body.jobId, ['failed', 'unknown', 'available']);
    expect(job.status).toBe('failed');
    expect(job.outputs).toHaveLength(0);
    expect(job.errors.some((error) => error.code === 'DOWNLOAD_FAILED')).toBe(true);
  });

  it('取消：只在服务确认后变为已取消', async () => {
    const created = await createJob({
      requestId: 'p4-cancel', providerId: 'fixture-local', modelId: 'fixture-video-1', kind: 'video.text',
      prompt: '要被取消的任务', output: { count: 1, durationSeconds: 2 }, providerOptions: { fixtureMode: 'slow' },
    });
    // 等任务进入生成阶段后再取消
    await waitForJob(created.body.jobId, ['submitted', 'generating', 'available', 'cancelling']);
    const cancel = await api(service.url, token, 'POST', `/v1/jobs/${created.body.jobId}/cancel`, {});
    expect(cancel.status).toBe(200);
    const job = await waitForJob(created.body.jobId, ['cancelled', 'failed']);
    expect(job.status).toBe('cancelled');
    expect([...job.notes].join(' ')).toContain('确认取消');
  });

  it('后台重启后恢复任务记录并继续查询既有服务任务（不重新提交）', async () => {
    const created = await createJob({
      requestId: 'p4-restart', providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text',
      prompt: '重启恢复', output: { count: 1 }, providerOptions: { fixtureMode: 'slow' },
    });
    const jobId = created.body.jobId;
    // 等它提交到服务并进入生成中
    const before = await waitForJob(jobId, ['generating', 'submitted', 'available']);
    const providerJobId = before.providerJobId;
    const attempts = before.attempts;
    await service.stop('SIGKILL');
    await delay(300);
    service = await startBackend(workspace, { name: 'p4-restart' });
    const session = await pairSession(service.url, readPairingKey(workspace), 'p4-media-ai-2');
    token = session.token;

    const after = await waitForJob(jobId, ['available', 'generating', 'submitted', 'failed']);
    expect(after.providerJobId).toBe(providerJobId); // 同一个服务端任务，没有重复提交
    expect(after.attempts).toBe(attempts);
    const finished = after.status === 'available' ? after : await waitForJob(jobId, ['available', 'failed']);
    expect(finished.status).toBe('available');
    trace('p4-restart', { jobId, providerJobId, attemptsBefore: attempts, attemptsAfter: finished.attempts, status: finished.status });
  }, 60000);
});

describe('P4 生成工具（经插件构建产物的真实 MCP 进程）', () => {
  it('列出服务 → 估价 → 提交 → 查询 → 取消与列表', async () => {
    const client = new McpClient(workspace, { clientName: 'p4-media-tools', env: { STUDIO_AUTOSTART: '0' }, name: 'p4-tools', entry: PLUGIN_BUNDLE });
    try {
      await client.request('initialize', { protocolVersion: '2025-06-18', capabilities: {} });
      const providers = await client.callTool('studio_list_media_providers', {});
      expect(providers.ok, JSON.stringify(providers.error)).toBe(true);
      expect((providers.data as { providers: { isTestAdapter: boolean }[] }).providers[0].isTestAdapter).toBe(true);

      const estimate = await client.callTool('studio_estimate_media', {
        projectId, providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text', prompt: '工具估价', output: { count: 2 },
      });
      expect(estimate.ok, JSON.stringify(estimate.error)).toBe(true);
      expect((estimate.data as { estimate: { currency: string } }).estimate.currency).toBe('fixture-point');

      const created = await client.callTool('studio_create_media_job', {
        projectId, providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text', prompt: '工具提交的任务', output: { count: 1 }, requestId: 'p4-tool-job',
      });
      expect(created.ok, JSON.stringify(created.error)).toBe(true);
      const jobId = (created.data as { jobId: string }).jobId;

      let job = await client.callTool('studio_get_job', { jobId });
      for (let attempt = 0; attempt < 60 && (job.data as { status: string }).status !== 'available'; attempt += 1) {
        await delay(150);
        job = await client.callTool('studio_get_job', { jobId });
      }
      expect((job.data as { status: string }).status, JSON.stringify(job.data)).toBe('available');
      const outputId = (job.data as { outputs: { outputId: string }[] }).outputs[0].outputId;

      const accepted = await client.callTool('studio_accept_media_output', { projectId, jobId, outputId, name: '工具登记素材', kind: 'cg' });
      expect(accepted.ok, JSON.stringify(accepted.error)).toBe(true);
      const assetId = (accepted.data as { assetId: string }).assetId;
      expect(assetId).toMatch(/^as_/);

      const jobs = await client.callTool('studio_list_jobs', { projectId });
      expect((jobs.data as { jobs: { jobId: string }[] }).jobs.some((item) => item.jobId === jobId)).toBe(true);

      const cancelling = await client.callTool('studio_create_media_job', {
        projectId, providerId: 'fixture-local', modelId: 'fixture-video-1', kind: 'video.text', prompt: '待取消', output: { count: 1, durationSeconds: 2 }, providerOptions: { fixtureMode: 'slow' }, requestId: 'p4-tool-cancel',
      });
      const cancelResult = await client.callTool('studio_cancel_job', { jobId: (cancelling.data as { jobId: string }).jobId });
      expect(cancelResult.ok, JSON.stringify(cancelResult.error)).toBe(true);
      expect(['cancelling', 'cancelled']).toContain((cancelResult.data as { status: string }).status);
      trace('p4-tools', { jobId, assetId, cancelStatus: (cancelResult.data as { status: string }).status });
    } finally {
      await client.close();
    }
  }, 60000);
});

describe('P4 生成模板接到生成请求（KI-031）', () => {
  it('templateId 用模板补齐提示词与参数；显式值优先；类型不一致或模板不存在会被拒绝', async () => {
    const base = String(((await api(service.url, token, 'GET', `/v1/projects/${projectId}`)).body as { revision?: unknown }).revision ?? '');

    // 建模板：图片、带负向提示词与参数
    const created = await api(service.url, token, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'recipe-template-1',
      projectId,
      baseRevision: base,
      operations: [{
        action: 'recipe.create',
        payload: { name: '雨夜塔门', taskType: 'image.text', prompt: '雨夜的旧塔，青石台阶', negativePrompt: '模糊', params: { steps: 30 } },
      }],
    });
    expect(created.status, JSON.stringify(created.body)).toBe(200);
    expect((created.body as { status: string }).status).toBe('applied');
    const recipeId = (created.body as { changedIds: string[] }).changedIds.find((id) => id.startsWith('rcp_')) as string;
    const afterRecipe = (created.body as { revision: string }).revision;

    // 用模板提交生成任务（不给 prompt）
    const job = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs`, {
      requestId: 'job-template-1',
      providerId: 'fixture-local',
      modelId: 'fixture-image-1',
      kind: 'image.text',
      templateId: recipeId,
      prompt: '',
      output: { count: 1, aspect: '1:1' },
    });
    expect(job.status, JSON.stringify(job.body)).toBe(201);
    const jobId = String((job.body as { jobId?: unknown }).jobId ?? '');
    const detail = await api(service.url, token, 'GET', `/v1/jobs/${jobId}`);
    // 任务摘要在存续期内给出“生成模板合并后的实际请求”，便于核对模板是否生效
    const record = detail.body as { templateId?: string | null; effectivePrompt?: string; effectiveNegativePrompt?: string | null; effectiveProviderOptions?: Record<string, unknown> };
    expect(record.templateId).toBe(recipeId);
    expect(record.effectivePrompt).toBe('雨夜的旧塔，青石台阶');
    expect(record.effectiveNegativePrompt).toBe('模糊');
    expect(record.effectiveProviderOptions?.steps).toBe(30);

    // 显式值优先：请求给了 prompt 与参数就用请求的，模板只补缺省项
    const explicit = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs`, {
      requestId: 'job-template-2',
      providerId: 'fixture-local',
      modelId: 'fixture-image-1',
      kind: 'image.text',
      templateId: recipeId,
      prompt: '请求里的提示词优先',
      providerOptions: { steps: 8 },
      output: { count: 1, aspect: '1:1' },
    });
    expect(explicit.status, JSON.stringify(explicit.body)).toBe(201);
    const explicitDetail = await api(service.url, token, 'GET', `/v1/jobs/${String((explicit.body as { jobId?: unknown }).jobId ?? '')}`);
    const explicitRequest = explicitDetail.body as { effectivePrompt?: string; effectiveNegativePrompt?: string | null; effectiveProviderOptions?: Record<string, unknown> };
    expect(explicitRequest.effectivePrompt).toBe('请求里的提示词优先');
    expect(explicitRequest.effectiveNegativePrompt).toBe('模糊');
    expect(explicitRequest.effectiveProviderOptions?.steps).toBe(8);

    // 模板不存在 → 明确报错
    const missing = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs`, {
      requestId: 'job-template-3',
      providerId: 'fixture-local',
      modelId: 'fixture-image-1',
      kind: 'image.text',
      templateId: 'rcp_missing00001',
      prompt: 'x',
      output: { count: 1, aspect: '1:1' },
    });
    expect(missing.status).toBe(404);
    expect((missing.body as { error?: { code?: string } }).error?.code).toBe('NOT_FOUND');

    // 任务类型不一致 → 明确拒绝（视频模板不能用于图片请求）
    const videoRecipe = await api(service.url, token, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'recipe-template-2',
      projectId,
      baseRevision: afterRecipe,
      operations: [{ action: 'recipe.create', payload: { name: '塔顶转场', taskType: 'video.text', prompt: '镜头推进' } }],
    });
    expect(videoRecipe.status).toBe(200);
    const videoRecipeId = (videoRecipe.body as { changedIds: string[] }).changedIds.find((id) => id.startsWith('rcp_')) as string;
    const mismatch = await api(service.url, token, 'POST', `/v1/projects/${projectId}/media-jobs`, {
      requestId: 'job-template-4',
      providerId: 'fixture-local',
      modelId: 'fixture-image-1',
      kind: 'image.text',
      templateId: videoRecipeId,
      prompt: '',
      output: { count: 1, aspect: '1:1' },
    });
    expect(mismatch.status).toBe(422);
    expect((mismatch.body as { error?: { code?: string } }).error?.code).toBe('VALIDATION_FAILED');
    trace('ki-031-template-job', { recipeId, jobId, explicitJobId: (explicit.body as { jobId: string }).jobId });
  }, 180000);
});
