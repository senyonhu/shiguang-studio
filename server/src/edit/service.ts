// 统一编辑服务：人工界面与 AI 工具的唯一写入路径。
// 负责作用域检查、幂等、基础版本检查、动作应用、整体校验、journal 提交、历史、撤销与变更事件。
import { appendFileSync, existsSync, realpathSync } from 'node:fs';
import path from 'node:path';
import type { StoryContent } from '../../../src/runtime/content.ts';
import { describeEvents, sceneOutline, validateStory } from '../../../src/runtime/content.ts';
import type { StudioIssue } from '../core/errors.ts';
import { StudioError } from '../core/errors.ts';
import { contentHash, sha256Hex } from '../core/canonical.ts';
import { ID_PREFIX, makeId } from '../core/ids.ts';
import type { FileChange } from '../store/doc.ts';
import { cloneContent, collectObjectIds, contentFileSet, diffFileSets, loadProject, planFromChangedIds, planFromContent, workingCopyOf } from '../store/doc.ts';
import { ProjectCache } from '../store/project-cache.ts';
import type { ChangeRecord, OperationPayload, ProjectStateRecord, TransactionRecord } from '../store/journal.ts';
import { appendChange, commitTransaction, emptyState, loadState, readHistory, recordIdempotency, recordObjectVersions } from '../store/journal.ts';
import { projectPaths } from '../store/paths.ts';
import { ensureDir, readBinary, readJsonIfExists, writeJsonDurable } from '../store/fsx.ts';
import type { ActionIO } from './actions.ts';
import { actionDefinition } from './actions.ts';
import { API_VERSION } from '../version.ts';

export type Scope = 'read' | 'edit' | 'generate' | 'preview' | 'export' | 'admin';

export interface ActorRef {
  actorId: string;
  label: string;
  sessionId: string;
  channel: 'human-ui' | 'mcp' | 'cli';
  scopes: Scope[];
  /** 只有服务内部（撤销补偿）可以提交 internal 动作 */
  allowInternal?: boolean;
}

export interface OperationInput {
  action: string;
  targetIds?: string[];
  payload?: Record<string, unknown>;
  /** 对象级期望版本：与 .studio/state.json 中该对象最后修改的提交序号比较 */
  expectedObjectRevision?: string;
}

export interface TransactionRequest {
  apiVersion?: string;
  requestId: string;
  projectId: string;
  baseRevision: string;
  operations: OperationInput[];
  summary?: string;
  /** 撤销事务时记录被撤销的事务 ID */
  undoOf?: string;
}

export interface EditResult {
  status: 'applied' | 'conflict' | 'rejected';
  requestId: string;
  projectId: string;
  revision: string;
  persistedRevision: string;
  changedIds: string[];
  issues: StudioIssue[];
  undoId?: string;
  transactionId?: string;
  sequence?: number;
  summary?: string;
  replayed?: boolean;
  errorCode?: string;
  persistence?: { filesWritten: number; filesDeleted: number; stateWritten: boolean };
}

export interface ChangeFeed {
  projectId: string;
  revision: string;
  sequence: number;
  oldestSequence: number;
  gap: boolean;
  changes: ChangeRecord[];
}

export interface ChangeEvent {
  projectId: string;
  revision: string;
  sequence: number;
  transactionId: string;
  actor: string;
  changedIds: string[];
  summary: string;
  at: string;
}

export interface EditServiceOptions {
  workspaceRoot: string;
  /** 允许导入素材的根目录（预先授权，AI 不能提交任意系统路径） */
  importRoots: string[];
  now?: () => string;
  /** 会话写入许可：返回 false 表示该会话已被人工暂停。 */
  sessionGuard?: (actor: ActorRef) => boolean;
  onChange?: (event: ChangeEvent) => void;
  /** 变更流保留条数上限（默认 500），超出后查询较早序号会返回 gap。 */
  changeLimit?: number;
  /** 额外授权来源：按工程返回目录（例如生成任务的候选区），用于登记生成产物。 */
  candidateRoots?: (projectId: string) => string[];
  /** 打开后每次提交都会用全量序列化结果校验增量保存计划（用于测试与排查漏写）。 */
  verifyPlan?: boolean;
  /** 关闭内容缓存（用于需要强制从磁盘重读的测试）。 */
  disableCache?: boolean;
}

export class EditService {
  private options: EditServiceOptions;
  private cache: ProjectCache;

  constructor(options: EditServiceOptions) {
    this.options = options;
    this.cache = new ProjectCache();
  }

  /** 内容缓存统计（命中/未命中）：用于性能测量与排查“是否真的用了缓存”。 */
  cacheStats(): { entries: number; hits: number; misses: number } {
    return this.cache.stats();
  }

  /**
   * 事务阶段计时：设置 STUDIO_TIMING_FILE 时把每个阶段的耗时按行追加到该文件（JSONL）。
   * 只在测量时启用，正常运行时为零开销。
   */
  private startTiming(): { mark: (phase: string) => void; finish: (extra?: Record<string, unknown>) => void } | null {
    const file = process.env.STUDIO_TIMING_FILE;
    if (!file) return null;
    const started = process.hrtime.bigint();
    let last = started;
    const phases: Record<string, number> = {};
    return {
      mark: (phase: string) => {
        const now = process.hrtime.bigint();
        phases[phase] = Number(now - last) / 1e6;
        last = now;
      },
      finish: (extra = {}) => {
        const total = Number(process.hrtime.bigint() - started) / 1e6;
        try {
          appendFileSync(file, `${JSON.stringify({ total: Math.round(total * 100) / 100, phases, ...extra })}\n`, 'utf8');
        } catch { /* 计时失败不影响业务 */ }
      },
    };
  }

  /** 读取工程内容：优先命中缓存；磁盘指纹变化（外部改动）时自动重新读取。 */
  private load(paths: ReturnType<EditService['paths']>): { content: StoryContent; files: Map<string, string | Buffer>; cached: boolean } {
    if (this.options.disableCache) {
      const fresh = loadProject(paths);
      return { content: fresh.content, files: fresh.files, cached: false };
    }
    return this.cache.load(paths);
  }

  private now(): string {
    return this.options.now ? this.options.now() : new Date().toISOString();
  }

  private paths(projectId: string) {
    return projectPaths(this.options.workspaceRoot, projectId);
  }

  private requireProject(projectId: string) {
    const paths = this.paths(projectId);
    if (!existsSync(paths.projectJson)) throw new StudioError('NOT_FOUND', `工程 ${projectId} 不存在`);
    return paths;
  }

  hasScope(actor: ActorRef, scope: Scope): boolean {
    return actor.scopes.includes(scope) || actor.scopes.includes('admin');
  }

  /** 读取已授权导入根下的文件；未授权路径一律拒绝。 */
  readAuthorizedFile(sourcePath: string, projectId?: string): { absPath: string; bytes: Buffer } {
    const candidate = path.resolve(sourcePath);
    const roots = [
      ...this.options.importRoots,
      ...(projectId && this.options.candidateRoots ? this.options.candidateRoots(projectId) : []),
    ].map((root) => {
      try { return realpathSync(path.resolve(root)); } catch { return path.resolve(root); }
    });
    let resolved: string;
    try { resolved = realpathSync(candidate); } catch { throw new StudioError('NOT_FOUND', `导入来源文件不存在：${sourcePath}`); }
    const allowed = roots.some((root) => {
      const relative = path.relative(root, resolved);
      return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
    });
    if (!allowed) {
      throw new StudioError('FORBIDDEN', `导入来源不在已授权目录内：${sourcePath}`, { details: { authorizedRootCount: roots.length } });
    }
    return { absPath: resolved, bytes: readBinary(resolved) };
  }

  snapshot(projectId: string): { revision: string; content: StoryContent; outline: ReturnType<typeof sceneOutline>; state: ProjectStateRecord } {
    const paths = this.requireProject(projectId);
    const loaded = this.load(paths);
    const state = loadState(paths, projectId);
    return { revision: state.revision, content: loaded.content, outline: sceneOutline(loaded.content), state };
  }

  sceneDetail(projectId: string, sceneId: string) {
    const { content, revision } = this.snapshot(projectId);
    const scene = content.scenes.find((item) => item.sceneId === sceneId);
    if (!scene) throw new StudioError('NOT_FOUND', `场景 ${sceneId} 不存在`);
    return { revision, scene, events: describeEvents(content, sceneId) };
  }

  validate(projectId: string) {
    const { content, revision } = this.snapshot(projectId);
    const issues = validateStory(content);
    return { revision, ok: issues.length === 0, issues };
  }

  history(projectId: string): TransactionRecord[] {
    const paths = this.requireProject(projectId);
    return readHistory(paths);
  }

  getTransactionResult(projectId: string, requestId: string): EditResult | null {
    const paths = this.requireProject(projectId);
    const state = loadState(paths, projectId);
    const entry = state.idempotency[requestId];
    if (!entry) return null;
    return { ...(entry.result as EditResult), replayed: true, revision: state.revision };
  }

  changes(projectId: string, after = 0): ChangeFeed {
    const paths = this.requireProject(projectId);
    const state = loadState(paths, projectId);
    const gap = after < state.oldestChangeSequence - 1;
    return {
      projectId,
      revision: state.revision,
      sequence: state.sequence,
      oldestSequence: state.oldestChangeSequence,
      gap,
      changes: state.changes.filter((change) => change.sequence > after),
    };
  }

  /**
   * 保存检查点：把当前修订与内容指纹写入 .studio/snapshots，并回读 .studio/state.json
   * 确认修订已经落盘（返回 persistedRevision 与 verifiedFromDisk，不谎报保存成功）。
   */
  checkpoint(projectId: string): {
    revision: string;
    persistedRevision: string;
    sequence: number;
    contentDigest: string;
    fileCount: number;
    snapshotPath: string;
    stateWritten: boolean;
    verifiedFromDisk: boolean;
    createdAt: string;
  } {
    const paths = this.requireProject(projectId);
    const loaded = this.load(paths);
    const files = contentFileSet(loaded.content);
    const entries = [...files.entries()]
      .map(([relPath, content]) => ({ relPath, sha256: sha256Hex(content as string) }))
      .sort((a, b) => (a.relPath < b.relPath ? -1 : 1));
    const contentDigest = sha256Hex(entries.map((entry) => `${entry.sha256}  ${entry.relPath}`).join('\n'));
    const state = loadState(paths, projectId);
    const persisted = readJsonIfExists<ProjectStateRecord>(paths.stateJson);
    const createdAt = this.now();
    const snapshotPath = `.studio/snapshots/${state.revision}-${createdAt.replace(/[:.]/g, '-')}.json`;
    ensureDir(paths.snapshotsDir);
    writeJsonDurable(path.join(paths.root, snapshotPath), {
      apiVersion: API_VERSION,
      projectId,
      revision: state.revision,
      sequence: state.sequence,
      contentDigest,
      fileCount: entries.length,
      files: entries,
      createdAt,
      note: '检查点记录修订与内容指纹；工程内容一致性由 journal 事务保证，检查点不用于覆盖后续修改。',
    }, 'checkpoint');
    return {
      revision: state.revision,
      persistedRevision: persisted?.revision ?? '',
      sequence: state.sequence,
      contentDigest,
      fileCount: entries.length,
      snapshotPath,
      stateWritten: true,
      verifiedFromDisk: (persisted?.revision ?? '') === state.revision,
      createdAt,
    };
  }

  applyTransaction(actor: ActorRef, request: TransactionRequest): EditResult {
    if (!this.hasScope(actor, 'edit')) throw new StudioError('SCOPE_REQUIRED', '当前会话没有 edit 作用域');
    if (!request.requestId || typeof request.requestId !== 'string') throw new StudioError('BAD_REQUEST', 'requestId 不能为空');
    if (!Array.isArray(request.operations) || request.operations.length === 0) throw new StudioError('BAD_REQUEST', 'operations 不能为空');
    if (request.operations.length > 200) throw new StudioError('PAYLOAD_TOO_LARGE', '单次事务操作数不能超过 200');

    const paths = this.requireProject(request.projectId);
    // 阶段计时（仅在设置 STUDIO_TIMING_FILE 时启用）：用于按真实规模定位每事务的成本分布。
    const timing = this.startTiming();
    const loaded = this.load(paths);
    timing?.mark('load');
    const state = loadState(paths, request.projectId);
    timing?.mark('state');
    const requestHash = contentHash({
      apiVersion: request.apiVersion ?? API_VERSION,
      projectId: request.projectId,
      // 幂等指纹只覆盖请求内容（操作列表），不含 baseRevision：
      // 这样版本冲突后按同一 requestId 重试不会误判为“同 ID 不同请求”。
      operations: request.operations,
    });

    const existing = state.idempotency[request.requestId];
    if (existing) {
      if (existing.requestHash !== requestHash) {
        throw new StudioError('REQUEST_ID_CONFLICT', `requestId ${request.requestId} 已用于不同请求内容`, {
          details: { requestId: request.requestId, existingAt: existing.at },
        });
      }
      return { ...(existing.result as EditResult), replayed: true };
    }

    if (request.baseRevision !== state.revision) {
      return {
        status: 'conflict',
        errorCode: 'REVISION_CONFLICT',
        requestId: request.requestId,
        projectId: request.projectId,
        revision: state.revision,
        persistedRevision: state.revision,
        changedIds: [],
        issues: [{
          code: 'REVISION_CONFLICT',
          message: `基础版本 ${request.baseRevision} 已过期，当前修订为 ${state.revision}`,
        }],
      };
    }

    // 暂停检查在提交点执行，人工暂停后 AI 会话无法再写入。
    if (this.options.sessionGuard && !this.options.sessionGuard(actor)) {
      throw new StudioError('SESSION_PAUSED', `会话 ${actor.sessionId} 已被暂停写入`);
    }

    // 按需工作副本：只克隆将要改动的对象（整份克隆在 10 万事件下是最大单项成本，见 KI-026）。
    // 候选集合 = 动作声明的 targetIds + 载荷中出现的所有对象 ID（含嵌套），父对象由 workingCopyOf 处理。
    const touchIds = new Set<string>();
    for (const operation of request.operations) {
      for (const id of operation.targetIds ?? actionDefinition(operation.action).targetIds(operation.payload ?? {})) touchIds.add(id);
      collectObjectIds(operation.payload, touchIds);
    }
    const pristine = this.options.verifyPlan ? cloneContent(loaded.content) : null;
    const next = workingCopyOf(loaded.content, touchIds);
    timing?.mark('clone');
    touchIds.size;
    const io: ActionIO = { now: this.now(), readAuthorizedFile: (sourcePath: string) => this.readAuthorizedFile(sourcePath, request.projectId) };
    const appliedOperations: OperationPayload[] = [];
    const inverseOperations: OperationPayload[] = [];
    const changedIds = new Set<string>();
    const binaryFiles: FileChange[] = [];

    // 对象级版本检查：生成任务或长事务绑定前，确认目标对象没有被后续提交改过。
    for (const operation of request.operations) {
      if (!operation.expectedObjectRevision) continue;
      const ids = operation.targetIds ?? actionDefinition(operation.action).targetIds(operation.payload ?? {});
      for (const objectId of ids) {
        const current = state.objectVersions[objectId];
        if (current && current !== operation.expectedObjectRevision) {
          return {
            status: 'conflict',
            errorCode: 'OBJECT_REVISION_CONFLICT',
            requestId: request.requestId,
            projectId: request.projectId,
            revision: state.revision,
            persistedRevision: state.revision,
            changedIds: [],
            issues: [{
              code: 'OBJECT_REVISION_CONFLICT',
              objectId,
              message: `对象 ${objectId} 的期望版本 ${operation.expectedObjectRevision} 已过期，最后修改于提交 ${current}`,
            }],
          };
        }
      }
    }

    for (const operation of request.operations) {
      const definition = actionDefinition(operation.action);
      if (definition.exposed === 'internal' && !actor.allowInternal) {
        throw new StudioError('VALIDATION_FAILED', `操作 ${operation.action} 不能由客户端直接提交`, { issues: [{ code: 'INTERNAL_ACTION', message: '该操作用于服务内部补偿' }] });
      }
      for (const scope of definition.scopes) {
        if (!this.hasScope(actor, scope as Scope)) throw new StudioError('SCOPE_REQUIRED', `操作 ${operation.action} 需要 ${scope} 作用域`);
      }
      const payload = operation.payload ?? {};
      let result;
      try {
        result = definition.apply(next, payload, io);
      } catch (error) {
        // 动作级校验失败返回结构化 rejected 结果；越权、会话暂停等仍然抛出。
        if (error instanceof StudioError && error.code === 'VALIDATION_FAILED') {
          return {
            status: 'rejected',
            errorCode: 'VALIDATION_FAILED',
            requestId: request.requestId,
            projectId: request.projectId,
            revision: state.revision,
            persistedRevision: state.revision,
            changedIds: [],
            issues: error.issues.length ? error.issues : [{ code: 'VALIDATION_FAILED', message: error.message }],
          };
        }
        throw error;
      }
      appliedOperations.push({ action: operation.action, targetIds: operation.targetIds ?? definition.targetIds(payload), payload });
      inverseOperations.unshift(...result.inverse);
      for (const id of result.changedIds) if (id) changedIds.add(id);
      for (const file of result.binaryFiles ?? []) binaryFiles.push(file);
    }

    // 增量校验：命中缓存说明这份内容来自本进程上次校验过的状态，
    // 只需对本次改动的场景做逐事件参数校验；未改动场景仍参与全局结构与引用检查。
    const touchedSceneIds = new Set<string>();
    for (const operation of appliedOperations) {
      const payload = operation.payload ?? {};
      if (typeof payload.sceneId === 'string' && payload.sceneId) touchedSceneIds.add(payload.sceneId);
    }
    for (const objectId of changedIds) if (objectId.startsWith('sc_')) touchedSceneIds.add(objectId);
    const skipScenes = loaded.cached ? new Set(next.scenes.filter((scene) => !touchedSceneIds.has(scene.sceneId)).map((scene) => scene.sceneId)) : undefined;
    timing?.mark('apply');
    const issues = validateStory(next, skipScenes ? { skipEventParamsForScenes: skipScenes } : {});
    timing?.mark('validate');
    if (issues.length) {
      return {
        status: 'rejected',
        errorCode: 'VALIDATION_FAILED',
        requestId: request.requestId,
        projectId: request.projectId,
        revision: state.revision,
        persistedRevision: state.revision,
        changedIds: [],
        issues: issues.slice(0, 50).map((issue) => ({ code: issue.code, message: `${issue.path}：${issue.message}`, objectId: issue.objectId, field: issue.field })),
      };
    }

    // 增量保存计划：只序列化与 changedIds 相关的文件；verifyPlan 打开时与全量结果比对。
    const contentChanges = planFromChangedIds(next, loaded.files, [...changedIds]);
    timing?.mark('plan');
    if (this.options.verifyPlan) {
      const full = planFromContent(loaded.files, next);
      const key = (change: FileChange): string => `${change.relPath}:${change.content === null ? 'delete' : sha256Hex(change.content as string).slice(0, 16)}`;
      const selectiveKeys = contentChanges.map(key).sort().join('|');
      const fullKeys = full.map(key).sort().join('|');
      if (selectiveKeys !== fullKeys) {
        throw new StudioError('INTERNAL_ERROR', '增量保存计划与全量计划不一致（changedIds 映射遗漏）', {
          details: { selective: contentChanges.map((change) => change.relPath), full: full.map((change) => change.relPath) },
        });
      }
      // 独立校验：与事务前的原始内容逐文件比对。任何“计划之外”的内容变化都说明按需克隆漏掉了对象
      // （漏克隆会让缓存在原地被改写，属于最危险的一类缺陷，必须显式失败而不是静默通过）。
      if (pristine) {
        const changedRels = new Set(contentChanges.map((change) => change.relPath));
        const pristineFiles = contentFileSet(pristine);
        for (const [rel, text] of contentFileSet(next)) {
          if (changedRels.has(rel)) continue;
          if (pristineFiles.get(rel) !== text) throw new StudioError('INTERNAL_ERROR', `按需克隆漏掉了被就地修改的对象：${rel}`);
        }
      }
    }
    const sequence = state.sequence + 1;
    const revision = String(sequence);
    const transactionId = makeId('txn');
    const at = io.now;
    const summary = request.summary ?? appliedOperations.map((operation) => operation.action).join(', ');
    const changedIdList = [...changedIds].sort();

    const historyRecord: TransactionRecord = {
      transactionId,
      requestId: request.requestId,
      sequence,
      revisionBefore: state.revision,
      revisionAfter: revision,
      actor: actor.actorId,
      sessionId: actor.sessionId,
      operations: appliedOperations,
      inverseOperations,
      changedIds: changedIdList,
      summary,
      at,
    };
    if (request.undoOf) historyRecord.undoOf = request.undoOf;

    const result: EditResult = {
      status: 'applied',
      requestId: request.requestId,
      projectId: request.projectId,
      revision,
      persistedRevision: revision,
      changedIds: changedIdList,
      issues: [],
      undoId: transactionId,
      transactionId,
      sequence,
      summary,
    };

    const nextState: ProjectStateRecord = {
      ...state,
      idempotency: { ...state.idempotency },
      idempotencyOrder: [...state.idempotencyOrder],
      changes: [...state.changes],
      objectVersions: { ...state.objectVersions },
    };
    nextState.revision = revision;
    nextState.sequence = sequence;
    nextState.lastTransactionId = transactionId;
    recordObjectVersions(nextState, changedIdList, sequence);
    // 持久化的幂等结果只保留对象 ID 摘要（完整列表在 .studio/history 的事务记录里），
    // 重放时用 changedIdCount 说明被截断的总数，避免 state.json 随事务规模无界增长。
    recordIdempotency(nextState, request.requestId, requestHash, {
      ...result,
      changedIds: changedIdList.slice(0, 20),
      changedIdCount: changedIdList.length,
    }, at);
    appendChange(nextState, {
      sequence,
      revision,
      transactionId,
      requestId: request.requestId,
      actor: actor.actorId,
      sessionId: actor.sessionId,
      changedIds: changedIdList,
      summary,
      at,
    }, this.options.changeLimit);

    ensureDir(paths.studioDir);
    const committed = commitTransaction(paths, {
      transactionId,
      requestId: request.requestId,
      state: nextState,
      history: historyRecord,
      contentChanges,
      extraChanges: binaryFiles,
    });
    timing?.mark('commit');
    result.persistence = { filesWritten: committed.written.length, filesDeleted: committed.deleted.length, stateWritten: true };

    // 提交成功后直接用新内容更新缓存（避免再次全量读取）。
    // 注意：缓存的文件集合只保存“内容文件”（与 loadProject 一致）；二进制素材是一次性写入、
    // 事务不会删除它们，把它们并进集合会让后续的差异计算误判为删除。
    if (!this.options.disableCache) {
      const nextFiles = new Map(loaded.files);
      for (const change of contentChanges) {
        if (change.content === null) nextFiles.delete(change.relPath);
        else nextFiles.set(change.relPath, change.content);
      }
      this.cache.store(paths, next, nextFiles);
    }
    timing?.mark('cache');
    timing?.finish({ status: 'applied', operations: appliedOperations.length, changed: changedIdList.length, cached: loaded.cached });

    this.options.onChange?.({
      projectId: request.projectId,
      revision,
      sequence,
      transactionId,
      actor: actor.actorId,
      changedIds: changedIdList,
      summary,
      at,
    });
    return result;
  }

  /** 受控撤销：生成补偿事务，检查后续依赖，不覆盖无关修改。 */
  undo(actor: ActorRef, request: { requestId: string; projectId: string; undoId: string; baseRevision?: string }): EditResult {
    if (!this.hasScope(actor, 'edit')) throw new StudioError('SCOPE_REQUIRED', '当前会话没有 edit 作用域');
    const paths = this.requireProject(request.projectId);
    const records = readHistory(paths);
    const state = loadState(paths, request.projectId);
    const reject = (errorCode: string, issues: StudioIssue[]): EditResult => ({
      status: 'rejected',
      errorCode,
      requestId: request.requestId,
      projectId: request.projectId,
      revision: state.revision,
      persistedRevision: state.revision,
      changedIds: [],
      issues,
    });
    const target = records.find((record) => record.transactionId === request.undoId);
    if (!target) return reject('UNDO_NOT_FOUND', [{ code: 'UNDO_NOT_FOUND', message: `找不到事务 ${request.undoId}` }]);
    if (records.some((record) => record.undoOf === target.transactionId)) {
      return reject('CONFLICT', [{ code: 'ALREADY_UNDONE', message: `事务 ${target.transactionId} 已经被撤销` }]);
    }
    // 依赖检查针对补偿操作真正会修改的对象：撤销一条插入不应因为同一场景里的其它新增而失败。
    const undoTargetIds = new Set<string>();
    for (const operation of target.inverseOperations) {
      for (const id of operation.targetIds ?? []) undoTargetIds.add(id);
    }
    const conflicts = records.filter((record) => record.sequence > target.sequence && record.changedIds.some((id) => undoTargetIds.has(id)));
    if (conflicts.length) {
      return reject('UNDO_DEPENDENCY_CONFLICT', conflicts.flatMap((record) => record.changedIds
        .filter((id) => undoTargetIds.has(id))
        .map((id) => ({
          code: 'UNDO_DEPENDENCY_CONFLICT',
          objectId: id,
          message: `对象 ${id} 在事务 ${record.transactionId}（第 ${record.sequence} 次提交：${record.summary}）之后被修改，撤销会覆盖后续修改`,
        }))));
    }
    if (!target.inverseOperations.length) {
      return reject('UNDO_NOT_FOUND', [{ code: 'NO_INVERSE', message: `事务 ${target.transactionId} 没有可用的补偿操作` }]);
    }
    return this.applyTransaction({ ...actor, allowInternal: true }, {
      apiVersion: API_VERSION,
      requestId: request.requestId,
      projectId: request.projectId,
      baseRevision: request.baseRevision ?? state.revision,
      operations: target.inverseOperations,
      summary: `撤销事务 ${target.transactionId}：${target.summary}`,
      undoOf: target.transactionId,
    });
  }
}

export function emptyProjectState(projectId: string): ProjectStateRecord {
  return emptyState(projectId);
}

export function newTransactionId(): string {
  return makeId('txn');
}

export { contentFileSet, diffFileSets, ID_PREFIX };
