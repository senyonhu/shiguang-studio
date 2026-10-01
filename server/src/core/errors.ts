// 统一错误类型：所有对外错误都带稳定 code，HTTP 路由 / MCP 工具 / 编辑服务共用。
export type StudioErrorCode =
  | 'BAD_REQUEST'
  | 'UNAUTHENTICATED'
  | 'FORBIDDEN'
  | 'SCOPE_REQUIRED'
  | 'NOT_FOUND'
  | 'CONFLICT'
  | 'REVISION_CONFLICT'
  | 'REQUEST_ID_CONFLICT'
  | 'PROJECT_EXISTS'
  | 'SESSION_PAUSED'
  | 'VALIDATION_FAILED'
  | 'UNSUPPORTED_CAPABILITY'
  | 'HISTORY_GAP'
  | 'UNDO_DEPENDENCY_CONFLICT'
  | 'UNDO_NOT_FOUND'
  | 'BACKEND_UNAVAILABLE'
  | 'SERVICE_ALREADY_RUNNING'
  | 'STORAGE_ERROR'
  | 'PAYLOAD_TOO_LARGE'
  | 'RENDER_UNAVAILABLE'
  | 'RENDER_FAILED'
  | 'INTERNAL_ERROR';

export interface StudioIssue {
  code: string;
  message: string;
  objectId?: string;
  field?: string;
}

const STATUS: Record<StudioErrorCode, number> = {
  BAD_REQUEST: 400,
  UNAUTHENTICATED: 401,
  FORBIDDEN: 403,
  SCOPE_REQUIRED: 403,
  NOT_FOUND: 404,
  CONFLICT: 409,
  REVISION_CONFLICT: 409,
  REQUEST_ID_CONFLICT: 409,
  PROJECT_EXISTS: 409,
  SESSION_PAUSED: 423,
  VALIDATION_FAILED: 422,
  UNSUPPORTED_CAPABILITY: 422,
  RENDER_UNAVAILABLE: 503,
  RENDER_FAILED: 500,
  HISTORY_GAP: 409,
  UNDO_DEPENDENCY_CONFLICT: 409,
  UNDO_NOT_FOUND: 404,
  BACKEND_UNAVAILABLE: 503,
  SERVICE_ALREADY_RUNNING: 409,
  STORAGE_ERROR: 507,
  PAYLOAD_TOO_LARGE: 413,
  INTERNAL_ERROR: 500,
};

export function httpStatusFor(code: StudioErrorCode): number {
  return STATUS[code] ?? 500;
}

export class StudioError extends Error {
  code: StudioErrorCode;
  issues: StudioIssue[];
  details: Record<string, unknown>;

  constructor(code: StudioErrorCode, message: string, options?: { issues?: StudioIssue[]; details?: Record<string, unknown> }) {
    super(message);
    this.name = 'StudioError';
    this.code = code;
    this.issues = options?.issues ?? [];
    this.details = options?.details ?? {};
  }

  toBody(): Record<string, unknown> {
    return { error: { code: this.code, message: this.message, issues: this.issues, details: this.details } };
  }
}

export function asStudioError(error: unknown): StudioError {
  if (error instanceof StudioError) return error;
  const message = error instanceof Error ? error.message : String(error);
  return new StudioError('INTERNAL_ERROR', message);
}
