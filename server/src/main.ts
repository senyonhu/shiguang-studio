// 后台服务进程入口：单实例锁、发现记录、优雅退出、崩溃恢复。
// 用法：
//   node server/src/main.ts --workspace <dir> [--port 0] [--host 127.0.0.1]
//   node server/src/main.ts --workspace <dir> --print-discovery
//   node server/src/main.ts --workspace <dir> --init-pairing
import { randomBytes } from 'node:crypto';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createStudioServer } from './api/server.ts';
import { capabilities, noteMemorySample } from './api/server.ts';
import { AuthService } from './auth/sessions.ts';
import { StudioError } from './core/errors.ts';
import { EditService } from './edit/service.ts';
import { recoverProject } from './store/journal.ts';
import { loadConfig } from './store/config.ts';
import { projectPaths, workspacePaths } from './store/paths.ts';
import { ensureDir, readJsonIfExists, removeFileIfExists, writeJsonDurable } from './store/fsx.ts';
import { defaultWorkspaceRoot, isProcessAlive, probeHealth, readDiscovery } from './lifecycle.ts';
import { clearDiscovery, discoveryTemplate, writeDiscovery } from './service-registry.ts';
import { readRegistry } from './store/workspace.ts';
import { PreviewService } from './preview/service.ts';
import { MediaJobService } from './media/jobs.ts';
import { API_VERSION, SERVICE_NAME, SERVICE_VERSION } from './version.ts';

/** 仓库根：用于定位展示器构建产物（dist-player）。 */
const REPO_ROOT = path.resolve(fileURLToPath(new URL('../..', import.meta.url)));

interface CliOptions {
  workspaceRoot: string;
  host: string;
  port: number;
  printDiscovery: boolean;
  initPairing: boolean;
  uiOrigins: string[];
}

function parseArgs(argv: string[]): CliOptions {
  const value = (flag: string, fallback: string): string => {
    const index = argv.indexOf(flag);
    return index >= 0 && index + 1 < argv.length ? argv[index + 1] : fallback;
  };
  const origins: string[] = [];
  for (let index = 0; index < argv.length; index += 1) {
    if (argv[index] === '--ui-origin' && argv[index + 1]) origins.push(argv[index + 1]);
  }
  return {
    workspaceRoot: path.resolve(value('--workspace', defaultWorkspaceRoot())),
    host: value('--host', '127.0.0.1'),
    port: Number(value('--port', '0')) || 0,
    printDiscovery: argv.includes('--print-discovery'),
    initPairing: argv.includes('--init-pairing'),
    uiOrigins: origins,
  };
}

export async function startService(options: CliOptions): Promise<{ url: string; port: number; instanceId: string; close: () => Promise<void> }> {
  const workspaceRoot = options.workspaceRoot;
  const paths = workspacePaths(workspaceRoot);
  ensureDir(workspaceRoot);
  ensureDir(paths.serviceDir);
  ensureDir(path.join(workspaceRoot, 'imports'));

  // 单实例：已有健康实例时不启动第二个写入进程。
  const existing = readDiscovery(workspaceRoot);
  if (existing) {
    const healthy = isProcessAlive(existing.pid) ? await probeHealth(existing.url) : { ok: false, body: null };
    if (healthy.ok && healthy.body?.instanceId === existing.instanceId) {
      throw new StudioError('SERVICE_ALREADY_RUNNING', `工作区已有后台实例在运行：${existing.url}（pid ${existing.pid}）`, {
        details: { url: existing.url, pid: existing.pid, instanceId: existing.instanceId },
      });
    }
    clearDiscovery(workspaceRoot, existing.instanceId);
  }

  const config = loadConfig(workspaceRoot);
  const auth = new AuthService(paths);
  const pairing = auth.ensurePairingKey();
  const registry = readRegistry(workspaceRoot);
  const recovery: Record<string, unknown> = {};
  for (const entry of registry.projects) {
    const project = projectPaths(workspaceRoot, entry.projectId);
    if (!existsSync(project.projectJson)) continue;
    const report = recoverProject(project);
    if (report.rolledBack.length || report.discarded.length || report.committed.length) recovery[entry.projectId] = report;
  }

  const instanceId = `svc_${randomBytes(6).toString('hex')}`;
  const startedAt = new Date().toISOString();
  // 变更事件先经过转发占位，服务创建完成后指向 SSE 推送函数。
  let emitChange: (event: unknown) => void = () => {};
  const mediaJobs = new MediaJobService({
    workspaceRoot,
    budgetLimit: config.mediaBudgetLimit,
    concurrency: config.mediaConcurrency,
    sessionGuard: (actor) => auth.isWritable(actor.sessionId),
  });
  const editService = new EditService({
    workspaceRoot,
    importRoots: config.importRoots,
    candidateRoots: (projectId) => [mediaJobs.candidateRoot(projectId)],
    sessionGuard: (actor) => auth.isWritable(actor.sessionId),
    onChange: (event) => { noteMemorySample(process.memoryUsage()); emitChange(event); },
    // STUDIO_VERIFY_PLAN=1 时，每次提交都会用事务前的原始内容做全量比对，
    // 把「按需克隆漏掉对象 → 缓存被就地改写」这类缺陷变成显式失败（测试与排查用；有额外开销）。
    verifyPlan: process.env.STUDIO_VERIFY_PLAN === '1',
    // 变更流保留条数（默认 500）：测试/演示可用 STUDIO_CHANGE_LIMIT 调小，验证 gap 路径。
    ...(process.env.STUDIO_CHANGE_LIMIT ? { changeLimit: Number(process.env.STUDIO_CHANGE_LIMIT) } : {}),
  });
  const studio = createStudioServer({
    workspaceRoot,
    repoRoot: REPO_ROOT,
    host: options.host,
    port: options.port,
    editService,
    auth,
    previews: new PreviewService({ workspaceRoot }),
    mediaJobs,
    uiOrigins: options.uiOrigins.length ? options.uiOrigins : undefined,
    instanceId,
    startedAt,
  });
  emitChange = (event: unknown) => studio.emitChange(event);

  // 生成任务由后台定时器推进（不依赖编辑器窗口）：重启后恢复记录并继续查询既有服务任务。
  for (const entry of registry.projects) {
    if (!existsSync(projectPaths(workspaceRoot, entry.projectId).projectJson)) continue;
    const recovered = mediaJobs.recoverOnStart(entry.projectId);
    if (recovered.recovered.length) process.stderr.write(`[studio-service] 生成任务恢复：${entry.projectId} 共 ${recovered.recovered.length} 个\n`);
  }
  // 内存峰值采样：每 5 秒一次（覆盖非提交时段），提交时另有采样。
const memoryTimer = setInterval(() => noteMemorySample(process.memoryUsage()), 5000);
memoryTimer.unref?.();

const pumpTimer = setInterval(() => {
    for (const entry of readRegistry(workspaceRoot).projects) {
      if (!existsSync(projectPaths(workspaceRoot, entry.projectId).projectJson)) continue;
      void mediaJobs.pump(entry.projectId).catch(() => { /* 下一轮继续 */ });
    }
  }, 400);
  pumpTimer.unref?.();

  await new Promise<void>((resolve, reject) => {
    studio.server.once('error', reject);
    studio.server.listen(options.port, options.host, () => resolve());
  });
  const port = studio.port;
  const url = studio.url;
  const record = discoveryTemplate(workspaceRoot, instanceId, process.pid, port, url, startedAt);
  writeDiscovery(workspaceRoot, record);
  writeJsonDurable(paths.lockFile, { ...record, lockNote: '本文件用于单实例判断，不包含凭据。' }, 'lock');

  const shutdown = async (signal: string): Promise<void> => {
    process.stderr.write(`[studio-service] 收到 ${signal}，正在关闭 ${url}\n`);
    await studio.close();
    clearDiscovery(workspaceRoot, instanceId);
    removeFileIfExists(paths.lockFile);
    process.exit(0);
  };
  process.on('SIGINT', () => { void shutdown('SIGINT'); });
  process.on('SIGTERM', () => { void shutdown('SIGTERM'); });
  process.on('SIGHUP', () => { void shutdown('SIGHUP'); });

  process.stderr.write(`[studio-service] ${SERVICE_NAME} ${SERVICE_VERSION} (api ${API_VERSION}) 监听 ${url}\n`);
  process.stderr.write(`[studio-service] 工作区 ${workspaceRoot}；工程 ${registry.projects.length} 个；配对密钥${pairing.created ? '已生成' : '已存在'}；importRoots=${config.importRoots.join(', ')}\n`);
  if (Object.keys(recovery).length) process.stderr.write(`[studio-service] 启动恢复：${JSON.stringify(recovery)}\n`);

  return {
    url,
    port,
    instanceId,
    close: async () => {
      await studio.close();
      clearDiscovery(workspaceRoot, instanceId);
      removeFileIfExists(paths.lockFile);
    },
  };
}

async function main(): Promise<void> {
  const options = parseArgs(process.argv.slice(2));
  if (options.initPairing) {
    const auth = new AuthService(workspacePaths(options.workspaceRoot));
    const result = auth.ensurePairingKey();
    const rotated = process.argv.includes('--rotate') ? auth.rotatePairingKey() : null;
    process.stdout.write(`${JSON.stringify({ workspace: options.workspaceRoot, keyId: rotated?.keyId ?? result.keyId, created: result.created, rotated: rotated !== null }, null, 2)}\n`);
    return;
  }
  if (options.printDiscovery) {
    const record = readDiscovery(options.workspaceRoot);
    if (!record) {
      process.stdout.write(`${JSON.stringify({ running: false, workspace: options.workspaceRoot }, null, 2)}\n`);
      process.exitCode = 1;
      return;
    }
    const healthy = isProcessAlive(record.pid) ? await probeHealth(record.url) : { ok: false, body: null };
    process.stdout.write(`${JSON.stringify({ running: healthy.ok, record, health: healthy.body }, null, 2)}\n`);
    if (!healthy.ok) process.exitCode = 1;
    return;
  }
  const service = await startService(options);
  // 直接运行时保持进程存活
  process.stdin.resume?.();
  void service;
}

export { capabilities as describeCapabilities };

const isMainModule = process.argv[1] ? path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url)) : false;
if (isMainModule) {
  main().catch((error: unknown) => {
    const message = error instanceof StudioError ? `${error.code}: ${error.message}` : (error as Error).stack ?? String(error);
    process.stderr.write(`[studio-service] 启动失败：${message}\n`);
    process.exit(1);
  });
}

export { readJsonIfExists };
