// 服务生命周期（只读 + 发现/启动）：发现记录读取、单实例判断辅助、后台自动启动与健康检查。
// 本模块刻意不引用任何写文件能力：MCP 工具适配进程只加载本模块，因此适配层物理上无法写工程文件。
// 发现记录的写入与清理在 server/src/service-registry.ts（仅后台服务进程使用）。
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { StudioError } from './core/errors.ts';
import { API_VERSION, SERVICE_NAME, SERVICE_VERSION } from './version.ts';
import { workspacePaths } from './store/paths.ts';
import { readJsonIfExists } from './store/fsread.ts';

export interface DiscoveryRecord {
  apiVersion: string;
  service: string;
  serviceVersion: string;
  instanceId: string;
  pid: number;
  port: number;
  url: string;
  workspace: string;
  startedAt: string;
  healthPath: string;
}

export function readDiscovery(workspaceRoot: string): DiscoveryRecord | null {
  return readJsonIfExists<DiscoveryRecord>(workspacePaths(workspaceRoot).runtimeJson);
}

export function isProcessAlive(pid: number): boolean {
  if (!pid || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === 'EPERM';
  }
}

export async function probeHealth(url: string, timeoutMs = 2000): Promise<{ ok: boolean; body: Record<string, unknown> | null }> {
  try {
    const response = await fetch(`${url}/v1/health`, { signal: AbortSignal.timeout(timeoutMs) });
    const body = await response.json() as Record<string, unknown>;
    return { ok: response.ok, body };
  } catch {
    return { ok: false, body: null };
  }
}

export interface EnsureServiceOptions {
  workspaceRoot: string;
  autostart: boolean;
  waitMs?: number;
  serviceEntry?: string;
}

export interface EnsureServiceResult {
  record: DiscoveryRecord;
  startedByClient: boolean;
}

/**
 * 找到可用后台；必要时按配置启动，失败返回 BACKEND_UNAVAILABLE（不退回界面自动化或直接改文件）。
 * 陈旧的发现记录（进程已退出或健康检查失败）由后台启动时的 startService 清理，本函数不写文件。
 */
export async function ensureService(options: EnsureServiceOptions): Promise<EnsureServiceResult> {
  const waitMs = options.waitMs ?? 20000;
  const existing = readDiscovery(options.workspaceRoot);
  if (existing) {
    const healthy = isProcessAlive(existing.pid) ? await probeHealth(existing.url) : { ok: false, body: null };
    if (healthy.ok && healthy.body?.instanceId === existing.instanceId) return { record: existing, startedByClient: false };
  }
  if (!options.autostart) {
    throw new StudioError('BACKEND_UNAVAILABLE', `后台未运行且未启用自动启动（工作区 ${options.workspaceRoot}）`, {
      details: { workspace: options.workspaceRoot, hint: '先运行 npm run studio:server 启动后台，或设置 STUDIO_AUTOSTART=1' },
    });
  }
  const entry = options.serviceEntry ?? fileURLToPath(new URL('./main.ts', import.meta.url));
  const child = spawn(process.execPath, [entry, '--workspace', options.workspaceRoot], {
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
  });
  child.unref();
  const deadline = Date.now() + waitMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 200));
    const record = readDiscovery(options.workspaceRoot);
    if (!record) continue;
    const healthy = await probeHealth(record.url);
    if (healthy.ok && healthy.body?.instanceId === record.instanceId) return { record, startedByClient: true };
  }
  throw new StudioError('BACKEND_UNAVAILABLE', `尝试启动后台失败（${waitMs} 毫秒内没有健康响应）`, {
    details: { workspace: options.workspaceRoot, entry },
  });
}

export function defaultWorkspaceRoot(): string {
  const fromEnv = process.env.STUDIO_WORKSPACE;
  if (fromEnv && fromEnv.trim()) return fromEnv.trim();
  return fileURLToPath(new URL('../../.studio-workspace', import.meta.url));
}

export function serviceEntryExists(): boolean {
  return existsSync(fileURLToPath(new URL('./main.ts', import.meta.url)));
}

export { API_VERSION, SERVICE_NAME, SERVICE_VERSION };
