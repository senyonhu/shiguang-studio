// 素材生成协议：请求、能力声明、适配器接口与任务状态。
// 类型与方案 8.3 的示意一致；真实服务接入属于未完成项，本文件只定义协议与本地校验。
export type MediaKind = 'image.text' | 'image.edit' | 'video.text' | 'video.image';

export type ReferenceRole = 'character' | 'style' | 'composition' | 'input' | 'mask' | 'firstFrame' | 'lastFrame';

export interface MediaReference {
  assetId: string;
  version: string;
  role: ReferenceRole;
}

export interface MediaOutputSpec {
  aspectRatio?: string;
  width?: number;
  height?: number;
  durationSeconds?: number;
  fps?: number;
  count: number;
  transparent?: boolean;
  audio?: 'none' | 'generated';
}

export interface MediaBinding {
  objectId: string;
  field: string;
  expectedRevision?: string;
  mode: 'candidate' | 'autoApply';
}

export interface MediaRequest {
  apiVersion: '1';
  requestId: string;
  projectId: string;
  providerId: string;
  modelId: string;
  kind: MediaKind;
  prompt: string;
  negativePrompt?: string;
  references: MediaReference[];
  output: MediaOutputSpec;
  seed?: number;
  providerOptions?: Record<string, unknown>;
  binding?: MediaBinding;
  budgetPolicyId?: string;
  /** 生成模板（recipes/<id>.json）：服务端读取后合并提示词与参数，请求里的显式值优先。 */
  templateId?: string;
}

export interface ModelCapabilities {
  modelId: string;
  label: string;
  kinds: MediaKind[];
  referenceRoles: ReferenceRole[];
  maxCount: number;
  supportsTransparent: boolean;
  aspectRatios: string[];
  durationSeconds?: { min: number; max: number };
  fps?: number[];
}

export type CostState = 'unknown' | 'estimated' | 'confirmed';

export interface CostEstimate {
  state: CostState;
  currency: string;
  estimated?: number;
  note?: string;
}

export interface ProviderCapabilities {
  providerId: string;
  label: string;
  /** adapter 类别：fixture 表示测试适配器，不能当成真实服务 */
  adapter: 'fixture' | 'cloud' | 'local' | 'workflow';
  isTestAdapter: boolean;
  models: ModelCapabilities[];
  cost: CostEstimate;
  supportsProgress: boolean;
  supportsCancel: boolean;
  supportsIdempotentSubmit: boolean;
  fetchedAt: string;
  notes: string[];
}

export interface ProviderSubmission {
  /** 服务端任务 ID；提交结果未知时为 null */
  providerJobId: string | null;
  state: 'accepted' | 'unknown';
  submittedAt: string;
  note?: string;
}

export interface ProviderJobStatus {
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'unknown';
  stage?: string;
  progress?: number;
  error?: { code: string; message: string };
  cost?: CostEstimate;
}

export interface ProviderOutput {
  outputId: string;
  fileName: string;
  mimeType: string;
  bytes: Buffer;
  /** 测试适配器产物必须标记为不可播放（例如只有容器头的占位视频） */
  playable: boolean;
  notes: string[];
}

export interface MediaProviderAdapter {
  capabilities(): Promise<ProviderCapabilities>;
  validate(request: MediaRequest): Promise<{ ok: boolean; issues: { code: string; message: string; field?: string }[] }>;
  estimate(request: MediaRequest): Promise<CostEstimate>;
  submit(request: MediaRequest): Promise<ProviderSubmission>;
  poll(providerJobId: string): Promise<ProviderJobStatus>;
  cancel?(providerJobId: string): Promise<{ cancelled: boolean; note?: string }>;
  resolveOutputs(providerJobId: string, request: MediaRequest): Promise<ProviderOutput[]>;
}

export type JobStatus =
  | 'queued' | 'awaiting-budget' | 'submitted' | 'generating' | 'downloading' | 'verifying'
  | 'available' | 'cancelling' | 'cancelled' | 'failed' | 'unknown';

export interface MediaJobOutput {
  outputId: string;
  fileName: string;
  mimeType: string;
  bytes: number;
  sha256: string;
  path: string;
  playable: boolean;
  decodeNotes: string[];
  registeredAssetId?: string;
  boundAt?: string;
  bindingIssue?: string;
}

export interface MediaJobError { code: string; message: string; at: string; retryable: boolean }

export interface MediaJobRecord {
  jobId: string;
  requestId: string;
  requestHash: string;
  projectId: string;
  actor: string;
  sessionId: string;
  kind: MediaKind;
  providerId: string;
  modelId: string;
  prompt: string;
  status: JobStatus;
  stage: string;
  createdAt: string;
  updatedAt: string;
  providerJobId: string | null;
  attempts: number;
  estimate: CostEstimate;
  actualCost?: CostEstimate;
  reservedBudget?: number;
  outputs: MediaJobOutput[];
  errors: MediaJobError[];
  binding?: MediaBinding;
  request: MediaRequest;
  notes: string[];
}

export interface MediaJobSummary {
  /** 生成模板与合并后的实际请求（便于核对模板是否生效，KI-031） */
  templateId?: string | null;
  effectivePrompt?: string;
  effectiveNegativePrompt?: string | null;
  effectiveProviderOptions?: Record<string, unknown>;
  jobId: string;
  requestId: string;
  projectId: string;
  status: JobStatus;
  stage: string;
  kind: MediaKind;
  providerId: string;
  modelId: string;
  createdAt: string;
  updatedAt: string;
  attempts: number;
  estimate: CostEstimate;
  actualCost?: CostEstimate;
  outputCount: number;
  outputs: { outputId: string; path: string; sha256: string; bytes: number; playable: boolean; boundAt?: string; registeredAssetId?: string; bindingIssue?: string }[];
  errors: MediaJobError[];
  notes: string[];
  binding?: MediaBinding;
  providerJobId: string | null;
  providerIsTestAdapter: boolean;
}
