// 接受候选产物的协调器：把「登记素材」与「绑定剧情」放进同一个编辑事务。
// 绑定使用对象级期望版本；目标对象已被后续提交改过时，整个事务被拒绝，候选仍保留在候选区。
import { StudioError } from '../core/errors.ts';
import { sha256Hex } from '../core/canonical.ts';
import type { ActorRef, EditService } from '../edit/service.ts';
import type { MediaJobService } from './jobs.ts';
import type { MediaJobSummary } from './types.ts';

export interface AcceptInput {
  projectId: string;
  jobId: string;
  outputId: string;
  name?: string;
  kind?: 'background' | 'character' | 'cg' | 'audio' | 'video';
  binding?: { sceneId: string; eventId: string; field: string; expectedObjectRevision?: string };
}

export interface AcceptResult {
  status: 'applied' | 'rejected';
  errorCode?: string;
  issues: { code: string; message: string; objectId?: string; field?: string }[];
  assetId?: string;
  transactionId?: string;
  revision?: string;
  bindingApplied: boolean;
  job: MediaJobSummary;
}

export function acceptMediaOutput(editService: EditService, mediaJobs: MediaJobService, actor: ActorRef, input: AcceptInput): AcceptResult {
  const summary = mediaJobs.getJob(input.projectId, input.jobId);
  if (summary.status !== 'available') {
    throw new StudioError('CONFLICT', `任务 ${input.jobId} 状态为 ${summary.status}，只有 available 的候选可以接受`);
  }
  const output = summary.outputs.find((item) => item.outputId === input.outputId);
  if (!output) throw new StudioError('NOT_FOUND', `候选产物 ${input.outputId} 不存在`);

  const sourcePath = mediaJobs.outputPath(input.projectId, input.jobId, input.outputId);
  const kind = input.kind ?? (summary.kind.startsWith('video') ? 'video' : 'background');
  // 候选身份由任务与 outputId 共同确定；沿用已登记 ID 以兼容旧任务的幂等重放。
  const assetId = output.registeredAssetId ?? `as_gen${sha256Hex(JSON.stringify([input.jobId, input.outputId])).slice(0, 24)}`;
  const operations: { action: string; payload: Record<string, unknown>; targetIds?: string[]; expectedObjectRevision?: string }[] = [
    {
      action: 'asset.registerGenerated',
      payload: {
        sourcePath,
        name: input.name ?? `${summary.modelId} 候选 ${input.outputId}`,
        kind,
        assetId,
        provenance: {
          providerId: summary.providerId,
          modelId: summary.modelId,
          jobId: summary.jobId,
          providerJobId: summary.providerJobId,
          testFixture: summary.providerIsTestAdapter,
          playable: output.playable,
          expectedSha256: output.sha256,
          generationKind: summary.kind,
        },
      },
    },
  ];
  if (input.binding) {
    operations.push({
      action: 'asset.bind',
      targetIds: [input.binding.eventId],
      ...(input.binding.expectedObjectRevision ? { expectedObjectRevision: input.binding.expectedObjectRevision } : {}),
      payload: { sceneId: input.binding.sceneId, eventId: input.binding.eventId, field: input.binding.field, assetId },
    });
  }

  const baseRevision = editService.snapshot(input.projectId).revision;
  const result = editService.applyTransaction(actor, {
    requestId: `accept-${input.jobId}-${input.outputId}`,
    projectId: input.projectId,
    baseRevision,
    operations,
    summary: `接受生成候选 ${input.outputId}（任务 ${input.jobId}）`,
  });

  if (result.status !== 'applied') {
    const issue = result.issues[0]?.message ?? '事务被拒绝';
    mediaJobs.recordBinding(input.projectId, input.jobId, input.outputId, false, issue);
    return {
      status: 'rejected',
      errorCode: result.errorCode ?? 'VALIDATION_FAILED',
      issues: result.issues,
      bindingApplied: false,
      job: mediaJobs.getJob(input.projectId, input.jobId),
    };
  }

  mediaJobs.recordRegistration(input.projectId, input.jobId, input.outputId, assetId, result.transactionId ?? '');
  if (input.binding) mediaJobs.recordBinding(input.projectId, input.jobId, input.outputId, true);
  return {
    status: 'applied',
    assetId,
    transactionId: result.transactionId,
    revision: result.revision,
    bindingApplied: Boolean(input.binding),
    issues: [],
    job: mediaJobs.getJob(input.projectId, input.jobId),
  };
}
