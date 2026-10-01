// 素材生成任务服务：统一任务协议、持久队列、状态机、预算、取消、重启恢复与候选产物入库。
// 服务本身不访问外部网络：具体服务差异由 MediaProviderAdapter 承担（当前只有测试适配器）。
import { existsSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { StudioError } from '../core/errors.ts';
import { contentHash, sha256Hex } from '../core/canonical.ts';
import { makeId } from '../core/ids.ts';
import { ensureDir, readJsonIfExists, writeJsonDurable, writeFileDurable } from '../store/fsx.ts';
import { projectPaths } from '../store/paths.ts';
import { loadProject } from '../store/doc.ts';
import type { StoryContent } from '../../../src/runtime/content.ts';
import { decodeAsset } from './decode.ts';
import { FIXTURE_PROVIDER_ID, FixtureMediaAdapter } from './adapters/fixture.ts';
import type { CostEstimate, MediaJobRecord, MediaJobSummary, MediaProviderAdapter, MediaRequest } from './types.ts';

export interface MediaBudget {
  currency: string;
  limit: number;
  reserved: number;
  spent: number;
  updatedAt: string;
}

export interface MediaServiceOptions {
  workspaceRoot: string;
  now?: () => string;
  /** 每小时/每批预算上限（0 表示未设置，不阻塞本地测试链路） */
  budgetLimit?: number;
  concurrency?: number;
  /** 新任务持久化前检查会话仍可写；已有任务的幂等查询不受影响。 */
  sessionGuard?: (actor: { actorId: string; sessionId: string }) => boolean;
  /** 候选产物落盘目录的相对路径前缀（在工程内，便于随后登记为素材） */
  candidatePrefix?: string;
}

const JOB_FILE = (jobId: string): string => `${jobId}.json`;


/**
 * 生成模板（KI-031）：把模板的提示词/负向提示词/参数合并进请求。
 * 规则：模板提供缺省值，请求里的显式非空值优先；模板不存在直接报错（不静默忽略）。
 */
function applyTemplate(request: MediaRequest, loadContent: () => StoryContent): MediaRequest {
  if (!request.templateId) return request;
  const content = loadContent();
  const recipe = (content.recipes ?? []).find((item) => item.recipeId === request.templateId);
  if (!recipe) throw new StudioError('NOT_FOUND', `生成模板 ${request.templateId} 不存在`);
  if (recipe.taskType !== request.kind) {
    throw new StudioError('VALIDATION_FAILED', `生成模板 ${recipe.recipeId} 的任务类型是 ${recipe.taskType}，与请求的 ${request.kind} 不一致`);
  }
  const requestOptions = request.providerOptions ?? {};
  const templateOptions = recipe.params ?? {};
  return {
    ...request,
    prompt: request.prompt?.trim() ? request.prompt : recipe.prompt,
    negativePrompt: request.negativePrompt?.trim() ? request.negativePrompt : recipe.negativePrompt,
    providerOptions: { ...templateOptions, ...requestOptions },
  };
}

export class MediaJobService {
  private options: MediaServiceOptions;
  private adapters = new Map<string, MediaProviderAdapter>();

  constructor(options: MediaServiceOptions) {
    this.options = options;
    this.adapters.set(FIXTURE_PROVIDER_ID, new FixtureMediaAdapter({
      now: options.now,
      store: {
        load: () => this.readProviderState(),
        save: (state) => this.writeProviderState(state),
      },
    }));
  }

  private now(): string {
    return this.options.now ? this.options.now() : new Date().toISOString();
  }

  private projectPaths(projectId: string) {
    const paths = projectPaths(this.options.workspaceRoot, projectId);
    if (!existsSync(paths.projectJson)) throw new StudioError('NOT_FOUND', `工程 ${projectId} 不存在`);
    return paths;
  }

  private jobsDir(projectId: string): string {
    return path.join(this.projectPaths(projectId).studioDir, 'jobs');
  }

  private providerStateFile(projectId: string): string {
    return path.join(this.jobsDir(projectId), 'provider-state.json');
  }

  private readProviderState(): Record<string, { providerJobId: string; mode: never; polls: number; submittedAt: string; cancelled: boolean }> {
    const file = this.providerStateFile(this.currentProject);
    return readJsonIfExists<Record<string, never>>(file) ?? {};
  }

  private writeProviderState(state: Record<string, unknown>): void {
    const file = this.providerStateFile(this.currentProject);
    ensureDir(path.dirname(file));
    writeJsonDurable(file, state, 'provider');
  }

  /** 适配器存取“服务端状态”时需要知道当前工程；每次外部调用前设置。 */
  private currentProject = '';

  private budgetFile(projectId: string): string {
    return path.join(this.jobsDir(projectId), 'budget.json');
  }

  readBudget(projectId: string): MediaBudget {
    const existing = readJsonIfExists<MediaBudget>(this.budgetFile(projectId));
    const limit = this.options.budgetLimit ?? 0;
    if (existing) return { ...existing, limit: limit || existing.limit };
    return { currency: 'fixture-point', limit, reserved: 0, spent: 0, updatedAt: this.now() };
  }

  private writeBudget(projectId: string, budget: MediaBudget): void {
    budget.updatedAt = this.now();
    writeJsonDurable(this.budgetFile(projectId), budget, 'budget');
  }

  listProviderIds(): string[] {
    return [...this.adapters.keys()];
  }

  /** 按 jobId 定位任务及其工程（jobId 全局唯一）。 */
  locateJob(jobId: string): { projectId: string; summary: MediaJobSummary } | null {
    const dir = path.join(this.options.workspaceRoot);
    if (!existsSync(dir)) return null;
    for (const name of readdirSync(dir)) {
      const candidate = path.join(dir, name, '.studio', 'jobs', JOB_FILE(jobId));
      if (!existsSync(candidate)) continue;
      const record = readJsonIfExists<MediaJobRecord>(candidate);
      if (record) return { projectId: record.projectId, summary: this.summarize(record) };
    }
    return null;
  }

  listProviders(): Promise<{ providerId: string; label: string; adapter: string; isTestAdapter: boolean }[]> {
    return Promise.all([...this.adapters.values()].map(async (adapter) => {
      const capabilities = await adapter.capabilities();
      return { providerId: capabilities.providerId, label: capabilities.label, adapter: capabilities.adapter, isTestAdapter: capabilities.isTestAdapter };
    }));
  }

  describeProvider(providerId: string) {
    const adapter = this.requireAdapter(providerId);
    return adapter.capabilities();
  }

  private requireAdapter(providerId: string): MediaProviderAdapter {
    const adapter = this.adapters.get(providerId);
    if (!adapter) {
      throw new StudioError('UNSUPPORTED_CAPABILITY', `未接入的生成服务：${providerId}`, {
        details: { known: [...this.adapters.keys()], hint: '当前只有明确标注的测试适配器；真实服务适配器尚未接入。' },
      });
    }
    return adapter;
  }

  async estimate(request: MediaRequest): Promise<{ estimate: CostEstimate; validation: { ok: boolean; issues: { code: string; message: string; field?: string }[] }; budget: MediaBudget; withinBudget: boolean }> {
    const adapter = this.requireAdapter(request.providerId);
    const validation = await adapter.validate(request);
    const estimate = await adapter.estimate(request);
    const budget = this.readBudget(request.projectId);
    const withinBudget = !budget.limit || budget.reserved + budget.spent + (estimate.estimated ?? 0) <= budget.limit;
    return { estimate, validation, budget, withinBudget };
  }

  listJobs(projectId: string): MediaJobSummary[] {
    const dir = this.jobsDir(projectId);
    if (!existsSync(dir)) return [];
    const summaries: MediaJobSummary[] = [];
    for (const name of readdirSync(dir)) {
      if (!name.endsWith('.json') || name === 'budget.json' || name === 'provider-state.json') continue;
      const record = readJsonIfExists<MediaJobRecord>(path.join(dir, name));
      if (record) summaries.push(this.summarize(record));
    }
    return summaries.sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
  }

  getJob(projectId: string, jobId: string): MediaJobSummary {
    return this.summarize(this.requireJob(projectId, jobId));
  }

  private requireJob(projectId: string, jobId: string): MediaJobRecord {
    const file = path.join(this.jobsDir(projectId), JOB_FILE(jobId));
    const record = readJsonIfExists<MediaJobRecord>(file);
    if (!record) throw new StudioError('NOT_FOUND', `任务 ${jobId} 不存在`);
    return record;
  }

  private saveJob(record: MediaJobRecord): void {
    record.updatedAt = this.now();
    const file = path.join(this.jobsDir(record.projectId), JOB_FILE(record.jobId));
    ensureDir(path.dirname(file));
    writeJsonDurable(file, record, 'job');
  }

  private summarize(record: MediaJobRecord): MediaJobSummary {
    return {
      jobId: record.jobId,
      requestId: record.requestId,
      projectId: record.projectId,
      status: record.status,
      stage: record.stage,
      kind: record.kind,
      providerId: record.providerId,
      modelId: record.modelId,
      // 生成模板合并后的“实际请求”：便于核对模板是否生效（KI-031）
      templateId: record.request.templateId ?? null,
      effectivePrompt: record.request.prompt,
      effectiveNegativePrompt: record.request.negativePrompt ?? null,
      effectiveProviderOptions: (record.request.providerOptions ?? {}) as Record<string, unknown>,
      createdAt: record.createdAt,
      updatedAt: record.updatedAt,
      attempts: record.attempts,
      estimate: record.estimate,
      actualCost: record.actualCost,
      outputCount: record.outputs.length,
      outputs: record.outputs.map((output) => ({
        outputId: output.outputId,
        path: output.path,
        sha256: output.sha256,
        bytes: output.bytes,
        playable: output.playable,
        boundAt: output.boundAt,
        registeredAssetId: output.registeredAssetId,
        bindingIssue: output.bindingIssue,
      })),
      errors: record.errors,
      notes: record.notes,
      binding: record.binding,
      providerJobId: record.providerJobId,
      providerIsTestAdapter: record.providerId === FIXTURE_PROVIDER_ID,
    };
  }

  /** 创建任务：校验 → 估价 → 预算预留 → 幂等 → 持久化排队。 */
  async createJob(actor: { actorId: string; sessionId: string }, rawRequest: MediaRequest): Promise<{ job: MediaJobSummary; replayed: boolean }> {
    // 生成模板（KI-031）：合并模板的提示词与参数后，再走原有的能力校验/预算/幂等流程
    this.currentProject = rawRequest.projectId;
    const request = applyTemplate(rawRequest, () => loadProject(this.projectPaths(rawRequest.projectId)).content);
    const adapter = this.requireAdapter(request.providerId);
    const requestHash = contentHash({ ...request, requestId: undefined });
    const existing = this.listJobs(request.projectId).find((job) => job.requestId === request.requestId);
    if (existing) {
      const record = this.requireJob(request.projectId, existing.jobId);
      if (record.requestHash !== requestHash) {
        throw new StudioError('REQUEST_ID_CONFLICT', `requestId ${request.requestId} 已用于不同的生成请求`, { details: { jobId: record.jobId } });
      }
      return { job: this.summarize(record), replayed: true };
    }

    this.assertWritable(actor);
    const validation = await adapter.validate(request);
    if (!validation.ok) {
      throw new StudioError('VALIDATION_FAILED', '生成请求未通过服务能力校验', { issues: validation.issues.map((issue) => ({ code: issue.code, message: issue.message, field: issue.field })) });
    }
    const estimate = await adapter.estimate(request);
    // validate/estimate 可以等待外部服务；人工暂停发生在等待期间时也不能创建任务。
    this.assertWritable(actor);
    const budget = this.readBudget(request.projectId);
    const reserved = estimate.estimated ?? 0;
    if (budget.limit && budget.reserved + budget.spent + reserved > budget.limit) {
      throw new StudioError('CONFLICT', `超出预算上限（已预留 ${budget.reserved}、已花费 ${budget.spent}、本次需要 ${reserved}，上限 ${budget.limit} ${budget.currency}）`, {
        details: { budget, estimate },
      });
    }

    this.currentProject = request.projectId;
    const jobId = makeId('job');
    const record: MediaJobRecord = {
      jobId,
      requestId: request.requestId,
      requestHash,
      projectId: request.projectId,
      actor: actor.actorId,
      sessionId: actor.sessionId,
      kind: request.kind,
      providerId: request.providerId,
      modelId: request.modelId,
      prompt: request.prompt,
      status: 'queued',
      stage: 'queued',
      createdAt: this.now(),
      updatedAt: this.now(),
      providerJobId: null,
      attempts: 0,
      estimate,
      reservedBudget: reserved,
      outputs: [],
      errors: [],
      binding: request.binding,
      request,
      notes: ['任务由后台任务服务管理；进程重启后会恢复并查询既有服务任务，不会重新提交。'],
    };
    this.saveJob(record);
    budget.reserved += reserved;
    this.writeBudget(request.projectId, budget);
    return { job: this.summarize(record), replayed: false };
  }

  private assertWritable(actor: { actorId: string; sessionId: string }): void {
    if (this.options.sessionGuard && !this.options.sessionGuard(actor)) {
      throw new StudioError('SESSION_PAUSED', '该 AI 会话已暂停，不能创建新的生成任务');
    }
  }

  /** 推进一步任务状态机；返回本次是否发生变化。外部由定时器或测试显式调用。 */
  async step(projectId: string, jobId: string): Promise<boolean> {
    this.currentProject = projectId;
    const record = this.requireJob(projectId, jobId);
    const adapter = this.requireAdapter(record.providerId);
    const before = `${record.status}:${record.stage}:${record.outputs.length}`;
    try {
      switch (record.status) {
        case 'queued': {
          record.attempts += 1;
          const submission = await adapter.submit(record.request);
          if (submission.state === 'unknown' || !submission.providerJobId) {
            record.status = 'unknown';
            record.stage = 'submit-unknown';
            record.errors.push({ code: 'SUBMIT_RESULT_UNKNOWN', message: submission.note ?? '提交结果未知', at: this.now(), retryable: false });
            record.notes.push('提交结果未知：不自动重复提交（避免重复付费），可按 requestId 核对或人工确认后重试。');
            break;
          }
          record.providerJobId = submission.providerJobId;
          record.status = 'submitted';
          record.stage = 'submitted';
          break;
        }
        case 'submitted':
        case 'generating': {
          if (!record.providerJobId) {
            record.status = 'unknown';
            record.stage = 'missing-provider-job';
            break;
          }
          const status = await adapter.poll(record.providerJobId);
          if (status.state === 'unknown') {
            record.status = 'unknown';
            record.stage = 'poll-unknown';
            record.errors.push({ code: 'PROVIDER_STATUS_UNKNOWN', message: status.error?.message ?? '服务状态未知', at: this.now(), retryable: true });
            break;
          }
          if (status.state === 'cancelled') { record.status = 'cancelled'; record.stage = 'cancelled'; break; }
          if (status.state === 'failed') {
            record.status = 'failed';
            record.stage = 'provider-failed';
            record.errors.push({ code: status.error?.code ?? 'PROVIDER_FAILED', message: status.error?.message ?? '服务返回失败', at: this.now(), retryable: true });
            this.releaseBudget(record);
            break;
          }
          if (status.state === 'running' || status.state === 'queued') {
            record.status = 'generating';
            record.stage = status.stage ?? 'generating';
            break;
          }
          record.status = 'downloading';
          record.stage = 'downloading';
          if (status.cost) record.actualCost = status.cost;
          break;
        }
        case 'downloading': {
          if (!record.providerJobId) { record.status = 'unknown'; record.stage = 'missing-provider-job'; break; }
          const outputs = await adapter.resolveOutputs(record.providerJobId, record.request);
          record.status = 'verifying';
          record.stage = 'verifying';
          record.notes.push(`已取得 ${outputs.length} 个候选产物，等待校验。`);
          for (const output of outputs) {
            const decoded = decodeAsset(output.bytes, record.kind.startsWith('video') ? 'video' : 'background');
            if (!decoded.ok) {
              record.errors.push({ code: 'OUTPUT_UNDECODABLE', message: `候选 ${output.outputId} 无法解码：${decoded.message}`, at: this.now(), retryable: false });
              continue;
            }
            const assetId = makeId('as');
            const ext = decoded.extension;
            const relPath = `${this.options.candidatePrefix ?? 'assets/candidates'}/${record.jobId}/${assetId}${ext}`;
            const abs = path.join(this.projectPaths(record.projectId).root, relPath);
            writeFileDurable(abs, output.bytes, record.jobId);
            record.outputs.push({
              outputId: output.outputId,
              fileName: output.fileName,
              mimeType: output.mimeType,
              bytes: output.bytes.length,
              sha256: sha256Hex(output.bytes),
              path: relPath,
              playable: output.playable,
              decodeNotes: [...decoded.notes, ...output.notes],
            });
          }
          break;
        }
        case 'verifying': {
          if (!record.outputs.length) {
            record.status = 'failed';
            record.stage = 'no-usable-output';
            record.errors.push({ code: 'NO_USABLE_OUTPUT', message: '没有可用的候选产物（全部无法解码）', at: this.now(), retryable: true });
            this.releaseBudget(record);
            break;
          }
          record.status = 'available';
          record.stage = 'available';
          record.notes.push('候选产物已下载、校验并登记到候选区；尚未绑定到剧情。');
          if (record.request.binding?.mode === 'autoApply') {
            record.notes.push('autoApply 请求已记录：绑定需要显式接受（accept），不会自动覆盖已修改的场景。');
          }
          break;
        }
        case 'cancelling': {
          if (!record.providerJobId) { record.status = 'cancelled'; record.stage = 'cancelled'; this.releaseBudget(record); break; }
          const result = adapter.cancel ? await adapter.cancel(record.providerJobId) : { cancelled: false, note: '适配器不支持取消' };
          if (result.cancelled) {
            record.status = 'cancelled';
            record.stage = 'cancelled';
            record.notes.push(result.note ?? '服务已确认取消');
            this.releaseBudget(record);
          } else {
            record.stage = 'cancel-pending';
            record.notes.push(`取消请求中：${result.note ?? '等待服务确认'}`);
          }
          break;
        }
        default:
          break;
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const retryable = /下载失败|download/i.test(message);
      record.status = retryable ? 'failed' : 'unknown';
      record.stage = retryable ? 'download-failed' : 'step-error';
      record.errors.push({ code: retryable ? 'DOWNLOAD_FAILED' : 'PROVIDER_STEP_ERROR', message, at: this.now(), retryable });
      if (record.status === 'failed') this.releaseBudget(record);
    }
    this.saveJob(record);
    return `${record.status}:${record.stage}:${record.outputs.length}` !== before;
  }

  /** 推进该工程所有未完成任务；重启恢复也走这里（不重新提交已提交的任务）。 */
  async pump(projectId: string, maxSteps = 50): Promise<{ advanced: number; pending: number }> {
    this.currentProject = projectId;
    // unknown 等待人工核对，保留费用预留但不占自动调度槽；取消后仍可继续推进。
    const runnable = ['queued', 'submitted', 'generating', 'downloading', 'verifying', 'cancelling'];
    const jobs = this.listJobs(projectId)
      .filter((job) => runnable.includes(job.status))
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    let advanced = 0;
    for (const job of jobs.slice(0, this.options.concurrency ?? 1)) {
      let steps = 0;
      for (;;) {
        const changed = await this.step(projectId, job.jobId);
        steps += 1;
        if (changed) advanced += 1;
        const current = this.getJob(projectId, job.jobId);
        if (['available', 'cancelled', 'failed', 'unknown'].includes(current.status) || steps >= 8) break;
        if (!changed && steps > 2) break;
        if (maxSteps <= steps) break;
      }
    }
    const pending = this.listJobs(projectId).filter((job) => !['available', 'cancelled', 'failed'].includes(job.status)).length;
    return { advanced, pending };
  }

  requestCancel(projectId: string, jobId: string): MediaJobSummary {
    this.currentProject = projectId;
    const record = this.requireJob(projectId, jobId);
    if (['available', 'cancelled', 'failed'].includes(record.status)) {
      throw new StudioError('CONFLICT', `任务 ${jobId} 处于 ${record.status}，不能取消`);
    }
    record.status = 'cancelling';
    record.stage = 'cancelling';
    record.notes.push('已请求取消；等待服务确认或本地任务停止后才标记为已取消。');
    this.saveJob(record);
    return this.summarize(record);
  }

  /**
   * 接受候选产物的登记结果由调用方（accept 协调器）回写：登记与绑定在同一个编辑事务中完成，
   * 绑定冲突时整个事务被拒绝，候选仍保留在候选区。
   */
  /** 登记候选产物为素材（由路由/工具调用编辑服务后回写结果）。 */
  recordRegistration(projectId: string, jobId: string, outputId: string, assetId: string, transactionId: string): void {
    const record = this.requireJob(projectId, jobId);
    const output = record.outputs.find((item) => item.outputId === outputId);
    if (!output) throw new StudioError('NOT_FOUND', `候选产物 ${outputId} 不存在`);
    output.registeredAssetId = assetId;
    record.notes.push(`候选 ${outputId} 已登记为素材 ${assetId}（事务 ${transactionId}）。`);
    this.saveJob(record);
  }

  recordBinding(projectId: string, jobId: string, outputId: string, applied: boolean, issue?: string): void {
    const record = this.requireJob(projectId, jobId);
    const output = record.outputs.find((item) => item.outputId === outputId);
    if (!output) throw new StudioError('NOT_FOUND', `候选产物 ${outputId} 不存在`);
    if (applied) {
      output.boundAt = this.now();
      record.notes.push(`候选 ${outputId} 已绑定到剧情（对象 ${record.binding?.objectId ?? '(未指定)'}）。`);
    } else {
      output.bindingIssue = issue ?? '绑定未生效';
      record.notes.push(`候选 ${outputId} 未绑定：${output.bindingIssue}`);
    }
    this.saveJob(record);
  }

  /** 候选产物的绝对路径（用于编辑服务登记素材）。 */
  outputPath(projectId: string, jobId: string, outputId: string): string {
    const record = this.requireJob(projectId, jobId);
    const output = record.outputs.find((item) => item.outputId === outputId);
    if (!output) throw new StudioError('NOT_FOUND', `候选产物 ${outputId} 不存在`);
    return path.join(this.projectPaths(projectId).root, output.path);
  }

  /** 候选目录（编辑服务把它视为已授权来源）。 */
  candidateRoot(projectId: string): string {
    return path.join(this.projectPaths(projectId).root, this.options.candidatePrefix ?? 'assets/candidates');
  }

  private releaseBudget(record: MediaJobRecord): void {
    const budget = this.readBudget(record.projectId);
    const reserved = record.reservedBudget ?? 0;
    if (!reserved) return;
    budget.reserved = Math.max(0, budget.reserved - reserved);
    if (record.status === 'available') budget.spent += record.actualCost?.estimated ?? reserved;
    record.reservedBudget = 0;
    this.writeBudget(record.projectId, budget);
  }

  /** 服务启动时的恢复：把进行中的任务重新排入队列（不重新提交）。 */
  recoverOnStart(projectId: string): { recovered: string[] } {
    this.currentProject = projectId;
    const recovered: string[] = [];
    for (const job of this.listJobs(projectId)) {
      if (['available', 'cancelled', 'failed'].includes(job.status)) continue;
      const record = this.requireJob(projectId, job.jobId);
      record.notes.push(`后台重启恢复：任务从 ${job.status} 继续查询既有服务任务。`);
      this.saveJob(record);
      recovered.push(job.jobId);
    }
    return { recovered };
  }
}

export { FIXTURE_PROVIDER_ID };
