// 集成测试脚手架：真实后台进程、真实 HTTP、真实 MCP stdio 进程。
// 所有跨进程请求写入证据目录（STUDIO_EVIDENCE_DIR），凭据字段脱敏。
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import http from 'node:http';
import { mkdtempSync, mkdirSync, appendFileSync, existsSync, readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const REPO_ROOT = path.resolve(fileURLToPath(new URL('../../..', import.meta.url)));
export const SERVER_ENTRY = path.join(REPO_ROOT, 'server', 'src', 'main.ts');
export const MCP_ENTRY = path.join(REPO_ROOT, 'server', 'src', 'mcp-stdio.ts');
export const EVIDENCE_DIR = process.env.STUDIO_EVIDENCE_DIR ?? '';

export interface ApiResponse { status: number; body: unknown }

export function makeWorkspace(prefix: string): string {
  return mkdtempSync(path.join(os.tmpdir(), `studio-it-${prefix}-`));
}

export function trace(name: string, record: Record<string, unknown>): void {
  if (!EVIDENCE_DIR) return;
  const dir = path.join(EVIDENCE_DIR, 'traces');
  mkdirSync(dir, { recursive: true });
  appendFileSync(path.join(dir, `${name}.jsonl`), `${JSON.stringify({ at: new Date().toISOString(), ...record })}\n`, 'utf8');
}

export function logPath(name: string): string | null {
  if (!EVIDENCE_DIR) return null;
  const dir = path.join(EVIDENCE_DIR, 'logs');
  mkdirSync(dir, { recursive: true });
  return path.join(dir, `${name}.log`);
}

function appendLog(file: string | null, chunk: string): void {
  if (!file) return;
  appendFileSync(file, chunk, 'utf8');
}

export interface ServiceHandle {
  url: string;
  port: number;
  pid: number;
  instanceId: string;
  workspaceRoot: string;
  child: ChildProcess;
  stderr: string;
  stop: (signal?: NodeJS.Signals) => Promise<void>;
}

export async function startBackend(workspaceRoot: string, options: { name?: string; env?: Record<string, string>; entry?: string; cwd?: string } = {}): Promise<ServiceHandle> {
  const log = logPath(`${options.name ?? 'backend'}-service`);
  const child = spawn(process.execPath, [options.entry ?? SERVER_ENTRY, '--workspace', workspaceRoot, '--port', '0'], {
    stdio: ['ignore', 'pipe', 'pipe'],
    ...(options.cwd ? { cwd: options.cwd } : {}),
    // 真实进程集成测试默认打开「按需克隆/增量保存」的全量比对守卫（见 EditServiceOptions.verifyPlan）
    env: { ...process.env, STUDIO_VERIFY_PLAN: '1', ...(options.env ?? {}) },
  });
  let stderr = '';
  child.stdout?.on('data', (chunk: Buffer) => appendLog(log, chunk.toString()));
  child.stderr?.on('data', (chunk: Buffer) => {
    stderr += chunk.toString();
    appendLog(log, chunk.toString());
  });
  const discoveryFile = path.join(workspaceRoot, '.studio-service', 'runtime.json');
  const deadline = Date.now() + 20000;
  let record: { url: string; port: number; pid: number; instanceId: string } | null = null;
  while (Date.now() < deadline) {
    await delay(100);
    if (existsSync(discoveryFile)) {
      try {
        record = JSON.parse(readFileSync(discoveryFile, 'utf8'));
      } catch { continue; }
      if (record) {
        const health = await fetch(`${record.url}/v1/health`).then((response) => response.json()).catch(() => null) as { instanceId?: string } | null;
        if (health?.instanceId === record.instanceId) break;
        record = null;
      }
    }
    if (child.exitCode !== null) throw new Error(`后台进程提前退出（code=${child.exitCode}）：${stderr}`);
  }
  if (!record) throw new Error(`等待后台启动超时：${stderr}`);
  return {
    url: record.url,
    port: record.port,
    pid: record.pid,
    instanceId: record.instanceId,
    workspaceRoot,
    child,
    get stderr() { return stderr; },
    stop: async (signal: NodeJS.Signals = 'SIGKILL') => {
      if (child.exitCode === null) {
        child.kill(signal);
        const stopDeadline = Date.now() + 5000;
        while (child.exitCode === null && Date.now() < stopDeadline) await delay(50);
      }
    },
  };
}

export function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function readPairingKey(workspaceRoot: string): string {
  const file = path.join(workspaceRoot, '.studio-service', 'credentials', 'pairing.key');
  return (JSON.parse(readFileSync(file, 'utf8')) as { pairingKey: string }).pairingKey;
}

export function readDiscoveryRaw(workspaceRoot: string): Record<string, unknown> | null {
  const file = path.join(workspaceRoot, '.studio-service', 'runtime.json');
  if (!existsSync(file)) return null;
  try { return JSON.parse(readFileSync(file, 'utf8')) as Record<string, unknown>; } catch { return null; }
}

export function discoveryHasSecrets(workspaceRoot: string): string[] {
  const record = readDiscoveryRaw(workspaceRoot);
  if (!record) return [];
  const text = JSON.stringify(record).toLowerCase();
  const suspicious = ['token', 'secret', 'pairingkey', 'password', 'authorization'];
  return suspicious.filter((needle) => text.includes(needle));
}

export async function api(url: string, token: string, method: 'GET' | 'POST' | 'DELETE', pathname: string, body?: unknown): Promise<ApiResponse> {
  const response = await fetch(`${url}${pathname}`, {
    method,
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(20000),
  });
  const text = await response.text();
  let parsed: unknown = null;
  try { parsed = text ? JSON.parse(text) : null; } catch { parsed = { raw: text.slice(0, 500) }; }
  return { status: response.status, body: parsed };
}

export async function pairSession(url: string, pairingKey: string, clientName: string, scopes = ['read', 'edit', 'generate', 'preview', 'export']): Promise<{ token: string; sessionId: string; actorId: string }> {
  const response = await fetch(`${url}/v1/sessions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ pairingKey, clientName, scopes, channel: 'mcp' }),
    signal: AbortSignal.timeout(10000),
  });
  const body = await response.json() as { token?: string; session?: { sessionId: string; actorId: string }; error?: { message?: string } };
  if (!response.ok || !body.token || !body.session) throw new Error(`配对失败：${body.error?.message ?? response.status}`);
  return { token: body.token, sessionId: body.session.sessionId, actorId: body.session.actorId };
}

export async function adminSession(url: string, workspaceRoot: string, name = 'admin-cli'): Promise<{ token: string; sessionId: string }> {
  const created = await pairSession(url, readPairingKey(workspaceRoot), name, ['read', 'edit', 'admin']);
  return { token: created.token, sessionId: created.sessionId };
}

interface Pending { resolve: (value: unknown) => void; reject: (error: Error) => void; method: string }

/** 最小 MCP stdio 客户端：真实换行分隔 JSON-RPC 2.0。 */
export class McpClient {
  private child: ChildProcess;
  private buffer = '';
  private pending = new Map<number, Pending>();
  private nextId = 1;
  private stderrText = '';
  readonly messages: Record<string, unknown>[] = [];

  constructor(workspaceRoot: string, options: { clientName?: string; env?: Record<string, string>; name?: string; entry?: string } = {}) {
    const log = logPath(`${options.name ?? 'mcp'}-stdio`);
    this.child = spawn(process.execPath, [options.entry ?? MCP_ENTRY, '--workspace', workspaceRoot, '--client-name', options.clientName ?? 'integration-test'], {
      stdio: ['pipe', 'pipe', 'pipe'],
      env: { ...process.env, ...(options.env ?? {}) },
    });
    this.child.stdout?.on('data', (chunk: Buffer) => this.onData(chunk.toString(), log));
    this.child.stderr?.on('data', (chunk: Buffer) => {
      this.stderrText += chunk.toString();
      appendLog(log, chunk.toString());
    });
  }

  get stderr(): string {
    return this.stderrText;
  }

  private onData(text: string, log: string | null): void {
    appendLog(log, text);
    this.buffer += text;
    let index = this.buffer.indexOf('\n');
    while (index >= 0) {
      const line = this.buffer.slice(0, index).trim();
      this.buffer = this.buffer.slice(index + 1);
      if (line) this.handleLine(line);
      index = this.buffer.indexOf('\n');
    }
  }

  private handleLine(line: string): void {
    let message: { id?: number; result?: unknown; error?: { message?: string } };
    try {
      message = JSON.parse(line);
    } catch {
      return;
    }
    this.messages.push(message as Record<string, unknown>);
    if (typeof message.id === 'number' && this.pending.has(message.id)) {
      const pending = this.pending.get(message.id)!;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(`${pending.method} 失败：${message.error.message}`));
      else pending.resolve(message.result);
    }
  }

  request(method: string, params: Record<string, unknown> = {}, timeoutMs = 20000): Promise<unknown> {
    const id = this.nextId;
    this.nextId += 1;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} 超时（${timeoutMs}ms）`));
      }, timeoutMs);
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timer); resolve(value); },
        reject: (error) => { clearTimeout(timer); reject(error); },
        method,
      });
      this.child.stdin?.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
    });
  }

  notify(method: string, params: Record<string, unknown> = {}): void {
    this.child.stdin?.write(`${JSON.stringify({ jsonrpc: '2.0', method, params })}\n`);
  }

  async callTool(name: string, args: Record<string, unknown> = {}): Promise<{ ok: boolean; tool: string; status?: string; data?: Record<string, unknown>; error?: { code: string; message: string; issues?: unknown[] }; notes?: string[] }> {
    const result = await this.request('tools/call', { name, arguments: args });
    const record = result as { structuredContent?: Record<string, unknown>; isError?: boolean };
    const structured = record.structuredContent ?? {};
    trace('mcp-calls', {
      tool: name,
      args: redact(args),
      ok: structured.ok,
      status: structured.status,
      error: structured.error,
      data: structured.data,
    });
    return structured as never;
  }

  async close(): Promise<void> {
    this.child.stdin?.end();
    await delay(50);
    if (this.child.exitCode === null) this.child.kill('SIGKILL');
  }
}

/** 原始 HTTP 请求：fetch 不允许设置 Host 等受限头，Host/Origin 校验需要用 node:http 直接发。 */
export function rawRequest(url: string, options: { method?: string; path?: string; headers?: Record<string, string> } = {}): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const target = new URL(url);
    const request = http.request({
      hostname: target.hostname,
      port: target.port,
      path: options.path ?? '/v1/health',
      method: options.method ?? 'GET',
      headers: options.headers,
    }, (response) => {
      let body = '';
      response.on('data', (chunk: Buffer) => { body += chunk.toString(); });
      response.on('end', () => resolve({ status: response.statusCode ?? 0, body }));
    });
    request.on('error', reject);
    request.end();
  });
}

export function redact(value: unknown): unknown {  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (/token|secret|key|password|authorization/i.test(key) && typeof item === 'string') out[key] = '<redacted>';
      else out[key] = redact(item);
    }
    return out;
  }
  return value;
}

/** 订阅 SSE 变更流，返回收到的变更事件与关闭函数。 */
export async function subscribeChanges(url: string, token: string, projectId: string, onEvent: (event: Record<string, unknown>) => void): Promise<() => void> {
  const controller = new AbortController();
  const response = await fetch(`${url}/v1/projects/${projectId}/events`, {
    headers: { authorization: `Bearer ${token}` },
    signal: controller.signal,
  });
  void (async () => {
    const reader = response.body?.getReader();
    if (!reader) return;
    const decoder = new TextDecoder();
    let buffer = '';
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let index = buffer.indexOf('\n\n');
        while (index >= 0) {
          const block = buffer.slice(0, index);
          buffer = buffer.slice(index + 2);
          for (const line of block.split('\n')) {
            if (line.startsWith('data: ')) {
              try { onEvent(JSON.parse(line.slice(6)) as Record<string, unknown>); } catch { /* ignore */ }
            }
          }
          index = buffer.indexOf('\n\n');
        }
      }
    } catch { /* aborted */ }
  })();
  await delay(150);
  return () => controller.abort();
}
