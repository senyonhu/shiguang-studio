// Browser-selected files enter the same authorised import and transaction path as other authors.
import { existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { sha256Hex } from '../core/canonical.ts';
import { isStudioId } from '../core/ids.ts';
import { StudioError } from '../core/errors.ts';
import type { ActorRef, EditResult, EditService } from '../edit/service.ts';
import type { OperationPayload } from '../store/journal.ts';
import { loadConfig } from '../store/config.ts';

export const MAX_UPLOAD_BYTES = 24 * 1024 * 1024;
export const UPLOAD_BODY_LIMIT = Math.ceil(MAX_UPLOAD_BYTES / 3) * 4 + 8192;
const KINDS = ['background', 'character', 'cg', 'audio', 'video', 'level'];

function text(body: Record<string, unknown>, key: string, max: number): string {
  const value = body[key];
  if (typeof value !== 'string' || !value.trim() || value.length > max) {
    throw new StudioError('BAD_REQUEST', `${key} 必须是非空字符串，最多 ${max} 个字符`);
  }
  return value.trim();
}

export function decodeUploadBase64(value: unknown): Buffer {
  if (typeof value === 'string' && value.length > Math.ceil(MAX_UPLOAD_BYTES / 3) * 4) throw new StudioError('PAYLOAD_TOO_LARGE', '单个文件不能超过 24 MB');
  if (typeof value !== 'string' || !value || value.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(value)) {
    throw new StudioError('BAD_REQUEST', '文件内容必须是有效的标准 Base64');
  }
  const bytes = Buffer.from(value, 'base64');
  if (!bytes.length || bytes.toString('base64') !== value) throw new StudioError('BAD_REQUEST', '文件内容不是规范 Base64');
  if (bytes.length > MAX_UPLOAD_BYTES) throw new StudioError('PAYLOAD_TOO_LARGE', '单个文件不能超过 24 MB');
  return bytes;
}

function inside(root: string, target: string): boolean {
  const rel = path.relative(root, target);
  return rel === '' || (!path.isAbsolute(rel) && rel !== '..' && !rel.startsWith(`..${path.sep}`));
}

export function applyUiUpload(edit: EditService, workspaceRoot: string, actor: ActorRef, projectId: string, body: Record<string, unknown>): EditResult & { assetId?: string; levelId?: string } {
  if (actor.channel !== 'human-ui') throw new StudioError('FORBIDDEN', '文件上传只允许人工编辑器会话');
  if (!isStudioId(projectId) || !projectId.startsWith('prj_')) throw new StudioError('BAD_REQUEST', '工程 ID 不正确');
  const requestId = text(body, 'requestId', 200);
  const baseRevision = text(body, 'baseRevision', 200);
  const kind = text(body, 'kind', 20);
  if (!KINDS.includes(kind)) throw new StudioError('BAD_REQUEST', '不支持的文件分类');
  const name = text(body, 'name', 120);
  const fileName = text(body, 'fileName', 200);
  if (fileName === '.' || fileName === '..' || /[\\/:\x00-\x1f]/.test(fileName) || path.basename(fileName) !== fileName) {
    throw new StudioError('BAD_REQUEST', '文件名必须是单个文件名，不能包含路径');
  }
  if (body.supportsSnapshot !== undefined && typeof body.supportsSnapshot !== 'boolean') throw new StudioError('BAD_REQUEST', 'supportsSnapshot 必须是布尔值');
  const bytes = decodeUploadBase64(body.dataBase64);
  const snapshot = edit.snapshot(projectId);
  const stable = sha256Hex(`${projectId}\n${requestId}`).slice(0, 24);
  const digest = sha256Hex(bytes);
  const extension = path.extname(fileName).toLowerCase();
  const imports = path.resolve(workspaceRoot, 'imports');
  if (existsSync(imports) && lstatSync(imports).isSymbolicLink()) throw new StudioError('FORBIDDEN', '上传暂存目录不能是符号链接');
  mkdirSync(imports, { recursive: true });
  const canonicalImports = realpathSync(imports);
  const authorised = loadConfig(workspaceRoot).importRoots.some((root) => {
    try { return inside(realpathSync(path.resolve(root)), canonicalImports); } catch { return false; }
  });
  if (!authorised) throw new StudioError('FORBIDDEN', '人工上传暂存目录未获授权');
  const sourcePath = path.join(imports, `ui-${stable}-${digest}${extension}`);
  let assetId: string | undefined;
  let levelId: string | undefined;
  let operations: OperationPayload[];
  if (kind === 'level') {
    if (!/\.html?$/.test(extension)) throw new StudioError('BAD_REQUEST', '关卡必须上传 HTML 文件');
    const html = bytes.toString('utf8');
    if (html.includes('\0') || !Buffer.from(html, 'utf8').equals(bytes)) throw new StudioError('BAD_REQUEST', '关卡 HTML 必须使用 UTF-8 编码');
    if (body.levelId !== undefined) {
      if (!isStudioId(body.levelId) || !body.levelId.startsWith('lv_')) throw new StudioError('BAD_REQUEST', '关卡 ID 不正确');
      if (!snapshot.content.levels.some((level) => level.levelId === body.levelId) && !edit.getTransactionResult(projectId, requestId)) throw new StudioError('NOT_FOUND', '要替换的关卡不存在');
      levelId = body.levelId;
    } else levelId = `lv_${stable}`;
    const entry = `levels/${levelId}.html`;
    operations = [
      { action: 'level.register', targetIds: [levelId], payload: { levelId, title: name, entry, supportsSnapshot: body.supportsSnapshot ?? true } },
      { action: 'level.installProgram', targetIds: [levelId], payload: { levelId, sourcePath, entry, uploadDigest: digest, originalFileName: fileName } },
    ];
  } else {
    if (body.levelId !== undefined) throw new StudioError('BAD_REQUEST', '素材上传不能指定关卡 ID');
    assetId = `as_${stable}`;
    operations = [{ action: 'asset.registerFromFile', targetIds: [assetId], payload: { assetId, name, kind, sourcePath, uploadDigest: digest, originalFileName: fileName } }];
  }
  // Deterministic payloads retain the transaction service's request fingerprint on retries.
  let created = false;
  try {
    if (existsSync(sourcePath)) {
      if (lstatSync(sourcePath).isSymbolicLink() || !lstatSync(sourcePath).isFile() || sha256Hex(readFileSync(sourcePath)) !== digest) {
        throw new StudioError('FORBIDDEN', '上传暂存文件不安全');
      }
    } else {
      writeFileSync(sourcePath, bytes, { flag: 'wx' });
      created = true;
    }
    const result = edit.applyTransaction(actor, { apiVersion: '1', projectId, requestId, baseRevision, operations, summary: kind === 'level' ? `导入关卡《${name}》` : `导入素材《${name}》` });
    return { ...result, ...(assetId ? { assetId } : {}), ...(levelId ? { levelId } : {}) };
  } finally {
    if (created) rmSync(sourcePath, { force: true });
  }
}
