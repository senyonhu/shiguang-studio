// 服务发现记录的写入与清理（只有后台服务进程使用；工具适配进程只读发现记录）。
import { API_VERSION, SERVICE_NAME, SERVICE_VERSION } from './version.ts';
import { workspacePaths } from './store/paths.ts';
import { ensureDir, removeFileIfExists, writeJsonDurable } from './store/fsx.ts';
import { readDiscovery } from './lifecycle.ts';
import type { DiscoveryRecord } from './lifecycle.ts';

export function writeDiscovery(workspaceRoot: string, record: DiscoveryRecord): void {
  const paths = workspacePaths(workspaceRoot);
  ensureDir(paths.serviceDir);
  writeJsonDurable(paths.runtimeJson, record, 'discovery');
}

export function clearDiscovery(workspaceRoot: string, instanceId?: string): void {
  const paths = workspacePaths(workspaceRoot);
  const current = readDiscovery(workspaceRoot);
  if (instanceId && current && current.instanceId !== instanceId) return;
  removeFileIfExists(paths.runtimeJson);
}

export function discoveryTemplate(workspaceRoot: string, instanceId: string, pid: number, port: number, url: string, startedAt: string): DiscoveryRecord {
  return {
    apiVersion: API_VERSION,
    service: SERVICE_NAME,
    serviceVersion: SERVICE_VERSION,
    instanceId,
    pid,
    port,
    url,
    workspace: workspaceRoot,
    startedAt,
    healthPath: '/v1/health',
  };
}
