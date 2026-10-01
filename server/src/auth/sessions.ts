// 认证与会话：操作者身份、作用域和暂停状态全部由后台会话决定，不接受客户端自报身份。
// 令牌只存 SHA-256 摘要；发现记录（runtime.json）不含任何凭据。
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { chmodSync, existsSync } from 'node:fs';
import type { ActorRef, Scope } from '../edit/service.ts';
import { StudioError } from '../core/errors.ts';
import { sha256Hex } from '../core/canonical.ts';
import { makeId, ID_PREFIX } from '../core/ids.ts';
import type { WorkspacePaths } from '../store/paths.ts';
import { ensureDir, readJsonIfExists, writeJsonDurable } from '../store/fsx.ts';

export const ALL_SCOPES: Scope[] = ['read', 'edit', 'generate', 'preview', 'export', 'admin'];
/** 本地人工界面可以取得的作用域（不含 admin） */
export const UI_SCOPES: Scope[] = ['read', 'edit', 'generate', 'preview', 'export'];
export const DEFAULT_AI_SCOPES: Scope[] = ['read', 'edit', 'generate', 'preview', 'export'];

export interface SessionRecord {
  sessionId: string;
  tokenHash: string;
  actorId: string;
  label: string;
  channel: 'human-ui' | 'mcp' | 'cli';
  scopes: Scope[];
  createdAt: string;
  lastSeenAt: string;
  paused: boolean;
  pausedAt?: string;
  clientName?: string;
}

interface SessionFile {
  apiVersion: string;
  sessions: SessionRecord[];
}

interface PairingMeta {
  apiVersion: string;
  keyId: string;
  keyHash: string;
  createdAt: string;
  note: string;
}

function readSessions(paths: WorkspacePaths): SessionFile {
  const file = readJsonIfExists<SessionFile>(paths.sessionsFile);
  if (file && Array.isArray(file.sessions)) return file;
  return { apiVersion: '1', sessions: [] };
}

function writeSessions(paths: WorkspacePaths, sessions: SessionFile): void {
  writeJsonDurable(paths.sessionsFile, sessions, 'sessions');
  try { chmodSync(paths.sessionsFile, 0o600); } catch { /* Windows 上忽略 */ }
}

function safeEqualHex(a: string, b: string): boolean {
  const bufA = Buffer.from(a, 'hex');
  const bufB = Buffer.from(b, 'hex');
  if (bufA.length !== bufB.length || bufA.length === 0) return false;
  return timingSafeEqual(bufA, bufB);
}

export class AuthService {
  private paths: WorkspacePaths;

  constructor(paths: WorkspacePaths) {
    this.paths = paths;
  }

  /** 首次启动时生成配对密钥（只在本机凭据目录保存一次）。 */
  ensurePairingKey(): { keyId: string; created: boolean } {
    ensureDir(this.paths.credentialsDir);
    const existing = readJsonIfExists<PairingMeta>(this.paths.pairingMetaFile);
    if (existing && existsSync(this.paths.pairingKeyFile)) return { keyId: existing.keyId, created: false };
    const key = randomBytes(32).toString('hex');
    const keyId = makeId(ID_PREFIX.session).replace('sess_', 'key_');
    writeJsonDurable(this.paths.pairingKeyFile, { apiVersion: '1', pairingKey: key, keyId, createdAt: new Date().toISOString(), note: '本机配对密钥：用于本机客户端创建有作用域的会话；不要提交到工程或发布包。' }, 'pairing');
    try { chmodSync(this.paths.pairingKeyFile, 0o600); } catch { /* Windows 上忽略 */ }
    writeJsonDurable(this.paths.pairingMetaFile, { apiVersion: '1', keyId, keyHash: sha256Hex(key), createdAt: new Date().toISOString(), note: '只保存配对密钥摘要。' }, 'pairing');
    try { chmodSync(this.paths.pairingMetaFile, 0o600); } catch { /* Windows 上忽略 */ }
    return { keyId, created: true };
  }

  readPairingKey(): string | null {
    const file = readJsonIfExists<{ pairingKey?: string }>(this.paths.pairingKeyFile);
    return file?.pairingKey ?? null;
  }

  rotatePairingKey(): { keyId: string } {
    const meta = readJsonIfExists<PairingMeta>(this.paths.pairingMetaFile);
    if (meta) {
      const key = randomBytes(32).toString('hex');
      const createdAt = new Date().toISOString();
      writeJsonDurable(this.paths.pairingKeyFile, { apiVersion: '1', pairingKey: key, keyId: meta.keyId, createdAt, note: '本机配对密钥' }, 'pairing');
      writeJsonDurable(this.paths.pairingMetaFile, { apiVersion: '1', keyId: meta.keyId, keyHash: sha256Hex(key), createdAt, note: '只保存配对密钥摘要。' }, 'pairing');
      return { keyId: meta.keyId };
    }
    return this.ensurePairingKey();
  }

  verifyPairingKey(key: string): boolean {
    const meta = readJsonIfExists<PairingMeta>(this.paths.pairingMetaFile);
    if (!meta || !key) return false;
    return safeEqualHex(sha256Hex(key), meta.keyHash);
  }

  createSession(input: { pairingKey: string; clientName: string; scopes?: Scope[]; channel?: SessionRecord['channel']; actorPrefix?: string }): { session: SessionRecord; token: string } {
    if (!this.verifyPairingKey(input.pairingKey)) throw new StudioError('UNAUTHENTICATED', '配对密钥无效');
    const clientName = (input.clientName ?? '').trim();
    if (!clientName || clientName.length > 60) throw new StudioError('BAD_REQUEST', 'clientName 必须是 1–60 字');
    const requested = input.scopes && input.scopes.length ? input.scopes : DEFAULT_AI_SCOPES;
    for (const scope of requested) {
      if (!ALL_SCOPES.includes(scope)) throw new StudioError('BAD_REQUEST', `未知作用域 ${scope}`);
    }
    const scopes = requested.filter((scope) => ALL_SCOPES.includes(scope));
    const token = randomBytes(32).toString('hex');
    const now = new Date().toISOString();
    const session: SessionRecord = {
      sessionId: makeId(ID_PREFIX.session),
      tokenHash: sha256Hex(token),
      actorId: `${input.actorPrefix ?? 'ai'}:${clientName}`,
      label: clientName,
      channel: input.channel ?? 'mcp',
      scopes,
      createdAt: now,
      lastSeenAt: now,
      paused: false,
      clientName,
    };
    const sessions = readSessions(this.paths);
    sessions.sessions.push(session);
    writeSessions(this.paths, sessions);
    return { session, token };
  }

  /** 本地人工界面会话：仅限 127.0.0.1 且 Host/Origin 已校验的请求调用。 */
  createUiSession(label = 'local-ui'): { session: SessionRecord; token: string } {
    const token = randomBytes(32).toString('hex');
    const now = new Date().toISOString();
    const session: SessionRecord = {
      sessionId: makeId(ID_PREFIX.session),
      tokenHash: sha256Hex(token),
      actorId: `human:${label}`,
      label,
      channel: 'human-ui',
      scopes: [...UI_SCOPES],
      createdAt: now,
      lastSeenAt: now,
      paused: false,
    };
    const sessions = readSessions(this.paths);
    sessions.sessions.push(session);
    writeSessions(this.paths, sessions);
    return { session, token };
  }

  authenticate(token: string): ActorRef {
    if (!token) throw new StudioError('UNAUTHENTICATED', '缺少会话令牌');
    const hash = sha256Hex(token);
    const sessions = readSessions(this.paths);
    const record = sessions.sessions.find((session) => safeEqualHex(session.tokenHash, hash));
    if (!record) throw new StudioError('UNAUTHENTICATED', '会话令牌无效');
    record.lastSeenAt = new Date().toISOString();
    writeSessions(this.paths, sessions);
    return {
      actorId: record.actorId,
      label: record.label,
      sessionId: record.sessionId,
      channel: record.channel,
      scopes: record.scopes,
    };
  }

  listSessions(): SessionRecord[] {
    return readSessions(this.paths).sessions.map((session) => ({ ...session, tokenHash: 'sha256:redacted' }));
  }

  setPaused(sessionId: string, paused: boolean): SessionRecord {
    const sessions = readSessions(this.paths);
    const record = sessions.sessions.find((session) => session.sessionId === sessionId);
    if (!record) throw new StudioError('NOT_FOUND', `会话 ${sessionId} 不存在`);
    record.paused = paused;
    if (paused) record.pausedAt = new Date().toISOString();
    else delete record.pausedAt;
    writeSessions(this.paths, sessions);
    return record;
  }

  revoke(sessionId: string): { revoked: boolean } {
    const sessions = readSessions(this.paths);
    const before = sessions.sessions.length;
    sessions.sessions = sessions.sessions.filter((session) => session.sessionId !== sessionId);
    writeSessions(this.paths, sessions);
    return { revoked: sessions.sessions.length !== before };
  }

  /** 提交点检查：会话存在且未被暂停。 */
  isWritable(sessionId: string): boolean {
    const record = readSessions(this.paths).sessions.find((session) => session.sessionId === sessionId);
    if (!record) return false;
    return !record.paused;
  }
}
