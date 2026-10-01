// MCP stdio 工具适配器：把后台 API 包装成 MCP 工具，供外部 AI 客户端调用。
// 传输：stdio + 换行分隔 JSON-RPC 2.0（stdout 只输出协议消息，日志一律走 stderr）。
// 客户端不直接读写工程文件；后台不可用时返回 BACKEND_UNAVAILABLE。
import { createInterface } from 'node:readline';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { StudioError } from './core/errors.ts';
import { ensureService, defaultWorkspaceRoot, readDiscovery } from './lifecycle.ts';
import { HttpApiClient } from './tools/http-client.ts';
import { TOOL_DEFINITIONS, toolByName } from './tools/registry.ts';
import { MCP_PROTOCOL_VERSIONS, SERVICE_VERSION } from './version.ts';
import { workspacePaths } from './store/paths.ts';
import { readJsonIfExists } from './store/fsread.ts';

interface JsonRpcRequest { jsonrpc: '2.0'; id?: number | string | null; method: string; params?: Record<string, unknown> }

const SERVER_INFO = { name: 'shiguang-studio-author', version: SERVICE_VERSION };

function log(...parts: unknown[]): void {
  process.stderr.write(`[studio-mcp] ${parts.map((part) => (typeof part === 'string' ? part : JSON.stringify(part))).join(' ')}\n`);
}

function write(message: unknown): void {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function reply(id: number | string | null | undefined, result: unknown): void {
  write({ jsonrpc: '2.0', id: id ?? null, result });
}

function replyError(id: number | string | null | undefined, code: number, message: string, data?: unknown): void {
  write({ jsonrpc: '2.0', id: id ?? null, error: { code, message, ...(data === undefined ? {} : { data }) } });
}

export interface McpOptions {
  workspaceRoot: string;
  autostart: boolean;
  clientName: string;
  scopes: string[];
}

export async function createSessionClient(options: McpOptions): Promise<HttpApiClient> {
  const ensured = await ensureService({ workspaceRoot: options.workspaceRoot, autostart: options.autostart });
  const paths = workspacePaths(options.workspaceRoot);
  const pairingFile = readJsonIfExists<{ pairingKey?: string }>(paths.pairingKeyFile);
  const pairingKey = pairingFile?.pairingKey;
  const direct = process.env.STUDIO_SESSION_TOKEN;
  let token = direct ?? '';
  const pair = async (): Promise<string> => {
    const key = readJsonIfExists<{ pairingKey?: string }>(paths.pairingKeyFile)?.pairingKey ?? pairingKey;
    if (!key) {
      throw new StudioError('UNAUTHENTICATED', `缺少配对密钥文件：${paths.pairingKeyFile}（先启动一次后台以生成）`);
    }
    const response = await fetch(`${ensured.record.url}/v1/sessions`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ pairingKey: key, clientName: options.clientName, scopes: options.scopes, channel: 'mcp' }),
      signal: AbortSignal.timeout(10000),
    });
    const body = await response.json() as { token?: string; error?: { message?: string } };
    if (!response.ok || !body.token) throw new StudioError('UNAUTHENTICATED', body.error?.message ?? '配对失败');
    return body.token;
  };
  if (!token) token = await pair();
  return new HttpApiClient(ensured.record.url, token, pair);
}

function toolResultToMcp(result: { ok: boolean; tool: string; status?: string; data?: unknown; error?: unknown; notes?: string[] }): Record<string, unknown> {
  const text = JSON.stringify(result, null, 2);
  return {
    content: [{ type: 'text', text }],
    structuredContent: result,
    isError: result.ok === false,
  };
}

export function createToolCaller(client: HttpApiClient, clientName: string) {
  return async (name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> => {
    const tool = toolByName(name);
    if (!tool) {
      return toolResultToMcp({ ok: false, tool: name, error: { code: 'UNKNOWN_TOOL', message: `未注册的工具 ${name}` } });
    }
    try {
      const result = await tool.handler({ client, clientName }, args ?? {});
      return toolResultToMcp(result);
    } catch (error) {
      const studioError = error instanceof StudioError ? error : new StudioError('INTERNAL_ERROR', (error as Error).message);
      return toolResultToMcp({ ok: false, tool: name, error: { code: studioError.code, message: studioError.message, issues: studioError.issues, details: studioError.details } });
    }
  };
}

async function main(): Promise<void> {
  const argv = process.argv.slice(2);
  const argValue = (flag: string, fallback: string): string => {
    const index = argv.indexOf(flag);
    return index >= 0 && index + 1 < argv.length ? argv[index + 1] : fallback;
  };
  const options: McpOptions = {
    workspaceRoot: argValue('--workspace', defaultWorkspaceRoot()),
    autostart: process.env.STUDIO_AUTOSTART !== '0',
    clientName: argValue('--client-name', process.env.STUDIO_CLIENT_NAME ?? 'mcp-client'),
    scopes: (process.env.STUDIO_SCOPES ?? 'read,edit,generate,preview,export').split(',').map((scope) => scope.trim()).filter(Boolean),
  };
  log(`工作区 ${options.workspaceRoot}，自动启动=${options.autostart}，客户端 ${options.clientName}`);

  let clientPromise: Promise<HttpApiClient> | null = null;
  const getClient = async (): Promise<HttpApiClient> => {
    if (!clientPromise) clientPromise = createSessionClient(options);
    try {
      return await clientPromise;
    } catch (error) {
      clientPromise = null;
      throw error;
    }
  };
  const callTool = async (name: string, args: Record<string, unknown>): Promise<Record<string, unknown>> => {
    let client: HttpApiClient;
    try {
      client = await getClient();
    } catch (error) {
      const studioError = error instanceof StudioError ? error : new StudioError('BACKEND_UNAVAILABLE', (error as Error).message);
      return toolResultToMcp({ ok: false, tool: name, error: { code: studioError.code, message: studioError.message, details: studioError.details } });
    }
    return createToolCaller(client, options.clientName)(name, args);
  };

  const handle = async (message: JsonRpcRequest): Promise<void> => {
    const { id, method, params } = message;
    switch (method) {
      case 'initialize': {
        const requested = typeof params?.protocolVersion === 'string' ? params.protocolVersion : '';
        const protocolVersion = MCP_PROTOCOL_VERSIONS.includes(requested) ? requested : MCP_PROTOCOL_VERSIONS[0];
        reply(id, {
          protocolVersion,
          capabilities: { tools: { listChanged: false } },
          serverInfo: SERVER_INFO,
          instructions: '拾光 Studio 制作工具：所有制作动作通过后台 API 完成。先用 studio_get_capabilities 与 studio_open_project 确认能力与修订，再用事务提交修改；重试时复用同一 requestId。',
        });
        return;
      }
      case 'notifications/initialized':
      case 'notifications/cancelled':
        return;
      case 'ping':
        reply(id, {});
        return;
      case 'tools/list':
        reply(id, {
          tools: TOOL_DEFINITIONS.map((tool) => ({
            name: tool.name,
            title: tool.title,
            description: tool.description,
            inputSchema: tool.inputSchema,
            annotations: { readOnlyHint: tool.scopes.length === 1 && tool.scopes[0] === 'read' },
          })),
        });
        return;
      case 'tools/call': {
        const name = String(params?.name ?? '');
        const args = (params?.arguments && typeof params.arguments === 'object' ? params.arguments : {}) as Record<string, unknown>;
        reply(id, await callTool(name, args));
        return;
      }
      case 'resources/list':
        reply(id, { resources: [] });
        return;
      case 'prompts/list':
        reply(id, { prompts: [] });
        return;
      default:
        replyError(id, -32601, `不支持的方法：${method}`);
    }
  };

  const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
  rl.on('line', (line) => {
    const text = line.trim();
    if (!text) return;
    let message: JsonRpcRequest;
    try {
      message = JSON.parse(text) as JsonRpcRequest;
    } catch {
      replyError(null, -32700, '消息不是有效 JSON');
      return;
    }
    void handle(message).catch((error: unknown) => {
      const studioError = error instanceof StudioError ? error : new StudioError('INTERNAL_ERROR', (error as Error).message);
      replyError(message.id ?? null, -32603, studioError.message, { code: studioError.code });
    });
  });
  rl.on('close', () => process.exit(0));

  // 后台可用性自检（不影响协议响应；失败时工具调用会返回 BACKEND_UNAVAILABLE）
  try {
    const client = await getClient();
    log(`已连接后台 ${client.baseUrl}（发现记录存在=${existsSync(workspacePaths(options.workspaceRoot).runtimeJson)}）`);
  } catch (error) {
    log(`后台暂不可用：${(error as Error).message}（工具调用将返回 BACKEND_UNAVAILABLE）`);
  }
  const discovery = readDiscovery(options.workspaceRoot);
  if (discovery) log(`发现记录：${discovery.url} pid=${discovery.pid}`);
}

const isMainModule = process.argv[1] ? path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url)) : false;
if (isMainModule) {
  void main();
}

export { main as runMcpStdio };
