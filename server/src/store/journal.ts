// 工程事务提交：多文件修改通过 journal 保证“要么全部生效、要么回滚到一致状态”。
// 每个事务的步骤：备份 → prepared 清单 → 内容文件写入 → 历史 → state.json → committed 标记 → 清理。
// 进程在任何一步被杀，下一次打开工程都会先执行 recoverProject 恢复一致状态。
import path from 'node:path';
import { existsSync } from 'node:fs';
import type { ProjectPaths } from './paths.ts';
import { copyFileDurable, ensureDir, fsyncDir, listDirNames, readJsonIfExists, removeDirRecursive, removeFileIfExists, safeRelName, writeFileDurable, writeJsonDurable } from './fsx.ts';
import type { FileChange } from './doc.ts';
import { StudioError } from '../core/errors.ts';

export interface ChangeRecord {
  sequence: number;
  revision: string;
  transactionId: string;
  requestId: string;
  actor: string;
  sessionId: string;
  /** 变更对象 ID 摘要（最多 CHANGE_CHANGED_IDS_LIMIT 个，完整列表见 .studio/history 的事务记录） */
  changedIds: string[];
  /** 本次事务实际影响的对象总数（可能大于 changedIds.length） */
  changedIdCount?: number;
  summary: string;
  at: string;
  undoOf?: string;
}

export interface OperationPayload {
  action: string;
  targetIds: string[];
  payload: Record<string, unknown>;
}

export interface TransactionRecord {
  transactionId: string;
  requestId: string;
  sequence: number;
  revisionBefore: string;
  revisionAfter: string;
  actor: string;
  sessionId: string;
  operations: OperationPayload[];
  inverseOperations: OperationPayload[];
  changedIds: string[];
  summary: string;
  at: string;
  undoOf?: string;
}

export interface IdempotencyEntry {
  requestHash: string;
  result: unknown;
  at: string;
}

export interface ProjectStateRecord {
  projectId: string;
  revision: string;
  sequence: number;
  lastTransactionId: string | null;
  idempotency: Record<string, IdempotencyEntry>;
  idempotencyOrder: string[];
  changes: ChangeRecord[];
  oldestChangeSequence: number;
  /** 对象 ID -> 最后修改该对象的提交序号（用于对象级版本检查） */
  objectVersions: Record<string, string>;
  /** objectVersions 的写入顺序（有界队列）：保证 state.json 体积不随工程规模无限增长 */
  objectVersionQueue?: [string, string][];
}

/** objectVersions 保留条数：足够覆盖“生成任务期间对象是否被改过”的检查窗口 */
export const OBJECT_VERSION_LIMIT = 2000;

export function emptyState(projectId: string): ProjectStateRecord {
  return {
    projectId,
    revision: '0',
    sequence: 0,
    lastTransactionId: null,
    idempotency: {},
    idempotencyOrder: [],
    changes: [],
    oldestChangeSequence: 1,
    objectVersions: {},
    objectVersionQueue: [],
  };
}

export const IDEMPOTENCY_LIMIT = 500;
export const CHANGE_LIMIT = 500;

export interface CommitPlan {
  transactionId: string;
  requestId: string;
  state: ProjectStateRecord;
  history: TransactionRecord;
  contentChanges: FileChange[];
  extraChanges?: FileChange[];
}

interface JournalFileEntry { relPath: string; existed: boolean; backup: string; kind: 'text' | 'binary' }

interface JournalManifest {
  transactionId: string;
  requestId: string;
  revisionBefore: string;
  revisionAfter: string;
  createdAt: string;
  status: 'prepared' | 'rolled_back' | 'committed';
  files: JournalFileEntry[];
}

function fault(name: string): void {
  if (process.env.STUDIO_FAULT_ENABLE !== '1') return;
  if (process.env.STUDIO_FAULT_POINT !== name) return;
  process.kill(process.pid, 'SIGKILL');
}

function isBuffer(value: unknown): value is Buffer {
  return Buffer.isBuffer(value);
}

/** 提交一个事务；返回实际写入/删除的文件列表（用于证据与调试）。 */
export function commitTransaction(paths: ProjectPaths, plan: CommitPlan): { written: string[]; deleted: string[] } {
  ensureDir(paths.journalDir);
  ensureDir(paths.historyDir);
  const txnDir = path.join(paths.journalDir, plan.transactionId);
  const beforeDir = path.join(txnDir, 'before');
  ensureDir(beforeDir);

  const historyRel = `.studio/history/${String(plan.state.sequence).padStart(6, '0')}-${plan.transactionId}.json`;
  const entries: { relPath: string; content: string | Buffer | null }[] = [];
  for (const change of plan.contentChanges) {
    entries.push({ relPath: change.relPath, content: change.content });
  }
  for (const change of plan.extraChanges ?? []) {
    entries.push({ relPath: change.relPath, content: change.content });
  }
  entries.push({ relPath: historyRel, content: `${JSON.stringify(plan.history, null, 2)}\n` });
  const stateRel = '.studio/state.json';
  entries.push({ relPath: stateRel, content: `${JSON.stringify(plan.state, null, 2)}\n` });

  // 1. 备份所有将被修改的文件，并写出 prepared 清单
  const manifest: JournalManifest = {
    transactionId: plan.transactionId,
    requestId: plan.requestId,
    revisionBefore: plan.history.revisionBefore,
    revisionAfter: plan.state.revision,
    createdAt: new Date().toISOString(),
    status: 'prepared',
    files: [],
  };
  for (const entry of entries) {
    const abs = path.join(paths.root, entry.relPath);
    const existed = existsSync(abs);
    const backup = safeRelName(entry.relPath);
    if (existed) copyFileDurable(abs, path.join(beforeDir, backup), plan.transactionId);
    manifest.files.push({ relPath: entry.relPath, existed, backup, kind: isBuffer(entry.content) ? 'binary' : 'text' });
  }
  writeJsonDurable(path.join(txnDir, 'manifest.json'), manifest, plan.transactionId);
  fault('after_backup');

  // 2. 写内容与二进制文件（state.json 与历史最后写）
  const written: string[] = [];
  const deleted: string[] = [];
  const contentEntries = entries.filter((entry) => entry.relPath !== stateRel && entry.relPath !== historyRel);
  let wroteAny = false;
  for (const entry of contentEntries) {
    const abs = path.join(paths.root, entry.relPath);
    if (entry.content === null) {
      removeFileIfExists(abs);
      deleted.push(entry.relPath);
    } else {
      writeFileDurable(abs, entry.content, plan.transactionId);
      written.push(entry.relPath);
    }
    if (!wroteAny) {
      wroteAny = true;
      fault('during_content');
    }
  }
  fault('after_content');

  // 3. 历史记录
  const historyEntry = entries.find((entry) => entry.relPath === historyRel)!;
  writeFileDurable(path.join(paths.root, historyRel), historyEntry.content as string, plan.transactionId);
  written.push(historyRel);
  fault('after_history');

  // 4. state.json（修订号与幂等记录）
  writeFileDurable(path.join(paths.root, stateRel), `${JSON.stringify(plan.state, null, 2)}\n`, plan.transactionId);
  written.push(stateRel);
  fsyncDir(paths.root);
  fault('after_state');

  // 5. committed 标记
  writeJsonDurable(path.join(txnDir, 'committed.json'), { transactionId: plan.transactionId, at: new Date().toISOString(), revisionAfter: plan.state.revision }, plan.transactionId);
  fault('after_marker');

  // 6. 清理备份
  cleanupCommitted(txnDir);
  return { written, deleted };
}

function cleanupCommitted(txnDir: string): void {
  removeDirRecursive(path.join(txnDir, 'before'));
  removeFileIfExists(path.join(txnDir, 'manifest.json'));
}

export interface RecoveryReport {
  committed: string[];
  rolledBack: string[];
  discarded: string[];
}

/** 打开工程前恢复一致状态。 */
export function recoverProject(paths: ProjectPaths): RecoveryReport {
  const report: RecoveryReport = { committed: [], rolledBack: [], discarded: [] };
  if (!existsSync(paths.journalDir)) return report;
  for (const name of listDirNames(paths.journalDir)) {
    const txnDir = path.join(paths.journalDir, name);
    if (!existsSync(txnDir)) continue;
    const committed = existsSync(path.join(txnDir, 'committed.json'));
    const manifest = readJsonIfExists<JournalManifest>(path.join(txnDir, 'manifest.json'));
    if (committed) {
      cleanupCommitted(txnDir);
      report.committed.push(name);
      continue;
    }
    if (!manifest) {
      // 备份阶段就被中断：还没有写入正式文件，直接丢弃。
      removeDirRecursive(txnDir);
      report.discarded.push(name);
      continue;
    }
    for (const file of manifest.files) {
      const abs = path.join(paths.root, file.relPath);
      if (file.existed) {
        const backup = path.join(txnDir, 'before', file.backup);
        if (existsSync(backup)) copyFileDurable(backup, abs, `recover-${name}`);
      } else {
        removeFileIfExists(abs);
      }
    }
    writeJsonDurable(path.join(txnDir, 'rolled_back.json'), { transactionId: name, at: new Date().toISOString(), files: manifest.files.length }, name);
    removeDirRecursive(path.join(txnDir, 'before'));
    removeFileIfExists(path.join(txnDir, 'manifest.json'));
    report.rolledBack.push(name);
  }
  return report;
}

export function readHistory(paths: ProjectPaths): TransactionRecord[] {
  if (!existsSync(paths.historyDir)) return [];
  const records: TransactionRecord[] = [];
  for (const name of listDirNames(paths.historyDir)) {
    if (!name.endsWith('.json')) continue;
    const record = readJsonIfExists<TransactionRecord>(path.join(paths.historyDir, name));
    if (record) records.push(record);
  }
  return records.sort((a, b) => a.sequence - b.sequence);
}

export function loadState(paths: ProjectPaths, projectId: string): ProjectStateRecord {
  const state = readJsonIfExists<ProjectStateRecord>(paths.stateJson);
  if (!state) return emptyState(projectId);
  if (!state.idempotency) state.idempotency = {};
  if (!state.idempotencyOrder) state.idempotencyOrder = [];
  if (!state.changes) state.changes = [];
  if (!state.objectVersions) state.objectVersions = {};
  if (!state.objectVersionQueue) state.objectVersionQueue = [];
  return state;
}

/**
 * 记录对象级版本，并保持有界：只保留最近 OBJECT_VERSION_LIMIT 个对象的版本记录。
 * 说明：更早的对象在 state.json 中没有版本记录，`expectedObjectRevision` 对它们无法判定过期，
 * 此时仍由事务级 baseRevision 提供保护（见 review/studio/known-issues.md 的 KI-029）。
 */
export function recordObjectVersions(state: ProjectStateRecord, objectIds: string[], sequence: number): void {
  const revision = String(sequence);
  const queue = state.objectVersionQueue ?? [];
  for (const objectId of objectIds) {
    if (!objectId) continue;
    state.objectVersions[objectId] = revision;
    queue.push([objectId, revision]);
  }
  while (queue.length > OBJECT_VERSION_LIMIT) {
    const [oldId, oldRevision] = queue.shift()!;
    if (state.objectVersions[oldId] === oldRevision) delete state.objectVersions[oldId];
  }
  state.objectVersionQueue = queue;
}

export function recordIdempotency(state: ProjectStateRecord, requestId: string, requestHash: string, result: unknown, at: string): void {
  state.idempotency[requestId] = { requestHash, result, at };
  state.idempotencyOrder = [...state.idempotencyOrder.filter((id) => id !== requestId), requestId];
  while (state.idempotencyOrder.length > IDEMPOTENCY_LIMIT) {
    const oldest = state.idempotencyOrder.shift()!;
    delete state.idempotency[oldest];
  }
}

export function appendChange(state: ProjectStateRecord, change: ChangeRecord, limit = CHANGE_LIMIT): void {
  // 变更记录只保留对象 ID 摘要：批量事务（例如一次 200 个事件）如果写入完整 ID 列表，
  // state.json 会随事务规模无界增长，而每个事务都要读写它（见 KI-026 的成本诊断）。
  const trimmed: ChangeRecord = {
    ...change,
    changedIds: change.changedIds.slice(0, CHANGE_CHANGED_IDS_LIMIT),
    changedIdCount: change.changedIdCount ?? change.changedIds.length,
  };
  state.changes = [...state.changes, trimmed];
  let truncated = false;
  while (state.changes.length > limit) {
    const removed = state.changes.shift()!;
    state.oldestChangeSequence = removed.sequence + 1;
    truncated = true;
  }
  if (!truncated && state.changes.length) state.oldestChangeSequence = state.changes[0].sequence;
}

/** 变更记录里保留的对象 ID 个数上限；完整列表在 .studio/history 的事务记录中。 */
export const CHANGE_CHANGED_IDS_LIMIT = 20;

export function requireState(paths: ProjectPaths, projectId: string): ProjectStateRecord {
  const state = readJsonIfExists<ProjectStateRecord>(paths.stateJson);
  if (!state) throw new StudioError('STORAGE_ERROR', `工程 ${projectId} 缺少 .studio/state.json`);
  return state;
}
