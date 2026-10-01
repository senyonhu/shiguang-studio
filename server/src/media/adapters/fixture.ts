// 测试适配器（fixture）：用于验证图片/视频生成协议链路，**不代表任何真实服务**。
// 明确标记：capabilities.isTestAdapter=true、adapter='fixture'；产物写入 provenance 时记录 testFixture。
// 图片产物是真实可解码的 PNG；视频产物只有 EBML 容器头（标记 playable=false），只用于协议验证。
import { sha256Hex } from '../../core/canonical.ts';
import { encodeSolidPng } from '../decode.ts';
import type {
  CostEstimate, MediaProviderAdapter, MediaRequest, ProviderCapabilities, ProviderJobStatus, ProviderOutput, ProviderSubmission,
} from '../types.ts';

export type FixtureMode = 'ok' | 'submit-timeout' | 'poll-unknown' | 'provider-error' | 'download-failure' | 'slow';

interface FixtureSubmission {
  providerJobId: string;
  mode: FixtureMode;
  polls: number;
  submittedAt: string;
  cancelled: boolean;
}

/** fixture 适配器的“服务端”状态：持久化在调用方给出的文件里，进程重启后仍能查询同一任务。 */
export interface FixtureStore {
  load(): Record<string, FixtureSubmission>;
  save(state: Record<string, FixtureSubmission>): void;
}

export const FIXTURE_PROVIDER_ID = 'fixture-local';
export const FIXTURE_IMAGE_MODEL = 'fixture-image-1';
export const FIXTURE_VIDEO_MODEL = 'fixture-video-1';

export class FixtureMediaAdapter implements MediaProviderAdapter {
  private store: FixtureStore;
  private now: () => string;

  constructor(options: { store: FixtureStore; now?: () => string }) {
    this.store = options.store;
    this.now = options.now ?? (() => new Date().toISOString());
  }

  async capabilities(): Promise<ProviderCapabilities> {
    return {
      providerId: FIXTURE_PROVIDER_ID,
      label: '本地测试适配器（fixture，不调用任何外部服务）',
      adapter: 'fixture',
      isTestAdapter: true,
      models: [
        {
          modelId: FIXTURE_IMAGE_MODEL,
          label: '测试图片模型（生成纯色 PNG）',
          kinds: ['image.text', 'image.edit'],
          referenceRoles: ['character', 'style', 'composition', 'input'],
          maxCount: 4,
          supportsTransparent: false,
          aspectRatios: ['1:1', '16:9', '9:16'],
        },
        {
          modelId: FIXTURE_VIDEO_MODEL,
          label: '测试视频模型（只生成容器头占位文件）',
          kinds: ['video.text', 'video.image'],
          referenceRoles: ['firstFrame', 'lastFrame', 'input'],
          maxCount: 1,
          supportsTransparent: false,
          aspectRatios: ['16:9'],
          durationSeconds: { min: 1, max: 10 },
          fps: [24, 30],
        },
      ],
      cost: { state: 'estimated', currency: 'fixture-point', estimated: 1, note: '测试适配器费用为固定占位值，不代表任何真实计费。' },
      supportsProgress: true,
      supportsCancel: true,
      supportsIdempotentSubmit: true,
      fetchedAt: this.now(),
      notes: [
        '这是测试适配器：不会访问网络，也不会产生真实调用记录或费用。',
        '图片产物是真实 PNG；视频产物只有 EBML 容器头，playable=false，仅用于协议验证。',
      ],
    };
  }

  async validate(request: MediaRequest): Promise<{ ok: boolean; issues: { code: string; message: string; field?: string }[] }> {
    const issues: { code: string; message: string; field?: string }[] = [];
    const capabilities = await this.capabilities();
    const model = capabilities.models.find((item) => item.modelId === request.modelId);
    if (!model) issues.push({ code: 'MODEL_UNKNOWN', message: `测试适配器没有模型 ${request.modelId}`, field: 'modelId' });
    else {
      if (!model.kinds.includes(request.kind)) issues.push({ code: 'KIND_UNSUPPORTED', message: `模型 ${model.modelId} 不支持 ${request.kind}`, field: 'kind' });
      if (request.output.count < 1 || request.output.count > model.maxCount) issues.push({ code: 'COUNT_UNSUPPORTED', message: `候选数量必须在 1–${model.maxCount} 之间`, field: 'output.count' });
      if (request.output.aspectRatio && !model.aspectRatios.includes(request.output.aspectRatio)) issues.push({ code: 'ASPECT_UNSUPPORTED', message: `不支持比例 ${request.output.aspectRatio}`, field: 'output.aspectRatio' });
      if (request.output.transparent && !model.supportsTransparent) issues.push({ code: 'TRANSPARENT_UNSUPPORTED', message: '测试适配器不支持透明背景', field: 'output.transparent' });
      const unsupportedRoles = request.references.filter((reference) => !model.referenceRoles.includes(reference.role));
      if (unsupportedRoles.length) issues.push({ code: 'REFERENCE_ROLE_UNSUPPORTED', message: `不支持参考素材角色：${unsupportedRoles.map((item) => item.role).join(', ')}`, field: 'references' });
      const mode = request.providerOptions?.fixtureMode;
      if (mode !== undefined && !['ok', 'submit-timeout', 'poll-unknown', 'provider-error', 'download-failure', 'slow'].includes(String(mode))) {
        issues.push({ code: 'FIXTURE_MODE_UNKNOWN', message: `未知的 fixtureMode：${String(mode)}`, field: 'providerOptions.fixtureMode' });
      }
    }
    if (!request.prompt || !request.prompt.trim()) issues.push({ code: 'PROMPT_EMPTY', message: '提示词不能为空', field: 'prompt' });
    if (request.prompt && request.prompt.length > 2000) issues.push({ code: 'PROMPT_TOO_LONG', message: '提示词最长 2000 字', field: 'prompt' });
    return { ok: issues.length === 0, issues };
  }

  async estimate(request: MediaRequest): Promise<CostEstimate> {
    const perCandidate = request.kind.startsWith('video') ? 3 : 1;
    return {
      state: 'estimated',
      currency: 'fixture-point',
      estimated: perCandidate * request.output.count,
      note: '测试适配器占位费用（不产生真实计费）。',
    };
  }

  async submit(request: MediaRequest): Promise<ProviderSubmission> {
    const mode = (request.providerOptions?.fixtureMode as FixtureMode | undefined) ?? 'ok';
    const state = this.store.load();
    const providerJobId = `fx_${request.projectId}_${request.requestId}`.replace(/[^a-z0-9_]/gi, '').slice(0, 60);
    // 幂等提交：同一 requestId 重复提交返回同一个 providerJobId，不产生第二个“服务端任务”。
    if (state[providerJobId]) {
      return { providerJobId, state: 'accepted', submittedAt: state[providerJobId].submittedAt, note: '重复提交：返回既有测试任务（幂等）' };
    }
    if (mode === 'submit-timeout') {
      return { providerJobId: null, state: 'unknown', submittedAt: this.now(), note: '测试适配器模拟“提交结果未知”：不写入任务状态，需按 requestId 核对而不是重复提交。' };
    }
    state[providerJobId] = { providerJobId, mode, polls: 0, submittedAt: this.now(), cancelled: false };
    this.store.save(state);
    return { providerJobId, state: 'accepted', submittedAt: state[providerJobId].submittedAt };
  }

  async poll(providerJobId: string): Promise<ProviderJobStatus> {
    const state = this.store.load();
    const record = state[providerJobId];
    if (!record) return { state: 'unknown', stage: 'unknown', error: { code: 'PROVIDER_JOB_UNKNOWN', message: '测试适配器没有该任务的记录' } };
    record.polls += 1;
    this.store.save(state);
    if (record.cancelled) return { state: 'cancelled', stage: 'cancelled' };
    if (record.mode === 'poll-unknown') return { state: 'unknown', stage: 'unknown', error: { code: 'PROVIDER_STATUS_UNKNOWN', message: '测试适配器模拟查询不到状态' } };
    if (record.mode === 'provider-error') return { state: 'failed', stage: 'generating', error: { code: 'PROVIDER_REJECTED', message: '测试适配器模拟服务拒绝' } };
    // slow：确实会在约 1.5 秒后成功（用于验证“生成中重启后台”的恢复路径，而不是永远运行）
    if (record.mode === 'slow') {
      const elapsed = Date.now() - Date.parse(record.submittedAt);
      if (elapsed < 1500) return { state: 'running', stage: 'generating', progress: Math.min(0.9, elapsed / 1500) };
      return { state: 'succeeded', stage: 'done', progress: 1, cost: { state: 'confirmed', currency: 'fixture-point', estimated: 1, note: '测试适配器结算（占位）' } };
    }
    if (record.polls < 2) return { state: 'running', stage: 'generating', progress: Math.min(0.5, record.polls / 4) };
    return { state: 'succeeded', stage: 'done', progress: 1, cost: { state: 'confirmed', currency: 'fixture-point', estimated: 1, note: '测试适配器结算（占位）' } };
  }

  async cancel(providerJobId: string): Promise<{ cancelled: boolean; note?: string }> {
    const state = this.store.load();
    const record = state[providerJobId];
    if (!record) return { cancelled: false, note: '测试适配器没有该任务记录' };
    record.cancelled = true;
    this.store.save(state);
    return { cancelled: true, note: '测试适配器已确认取消' };
  }

  async resolveOutputs(providerJobId: string, request: MediaRequest): Promise<ProviderOutput[]> {
    const state = this.store.load();
    const record = state[providerJobId];
    const mode = record?.mode ?? 'ok';
    if (record?.cancelled) throw new Error('任务已取消，不再下载产物');
    if (mode === 'download-failure') throw new Error('测试适配器模拟下载失败');
    const outputs: ProviderOutput[] = [];
    for (let index = 0; index < request.output.count; index += 1) {
      if (request.kind.startsWith('image')) {
        const color: [number, number, number] = [
          parseInt(sha256Hex(`${request.prompt}:${index}`).slice(0, 2), 16),
          parseInt(sha256Hex(`${request.prompt}:${index}`).slice(2, 4), 16),
          parseInt(sha256Hex(`${request.prompt}:${index}`).slice(4, 6), 16),
        ];
        const width = request.output.width ?? 64;
        const height = request.output.height ?? 36;
        outputs.push({
          outputId: `out_${index + 1}`,
          fileName: `fixture-image-${index + 1}.png`,
          mimeType: 'image/png',
          bytes: encodeSolidPng(width, height, color),
          playable: true,
          notes: ['测试适配器生成的纯色 PNG（可解码、可显示）'],
        });
      } else {
        // 只有 EBML 头 + 少量填充：仅用于验证协议链路，不能播放。
        const header = Buffer.from([0x1a, 0x45, 0xdf, 0xa3, 0x81, 0x00]);
        const padding = Buffer.alloc(64, 0);
        outputs.push({
          outputId: `out_${index + 1}`,
          fileName: `fixture-video-${index + 1}.webm`,
          mimeType: 'video/webm',
          bytes: Buffer.concat([header, padding]),
          playable: false,
          notes: ['测试适配器占位视频：只有 EBML 容器头，不含真实轨道，不能播放'],
        });
      }
    }
    return outputs;
  }
}
