// 发布组验证：建一个样板工程 → 导出发布包 → 运行发布包验收检查（含实际启动冒烟）。
// 用法：node scripts/studio-release-run.mjs [--out <目录>] [--keep-workspace]
// 说明：这是可复跑的发布验收入口；报告写入 <out>/outputs/，失败时退出码非 0。
import { spawn } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { argValue } from './studio-lib.mjs';
import { buildStory, call } from '../tests/studio/helpers/story.ts';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const outArg = argValue(process.argv, '--out', 'artifacts/release-tmp');
const outDir = path.resolve(ROOT, outArg);
const keepWorkspace = process.argv.includes('--keep-workspace');

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function run(command, args, options = {}) {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd: options.cwd ?? ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

async function startBackend(workspace) {
  const child = spawn(process.execPath, [path.join(ROOT, 'server', 'src', 'main.ts'), '--workspace', workspace, '--port', '0'], { stdio: ['ignore', 'pipe', 'pipe'] });
  const discovery = path.join(workspace, '.studio-service', 'runtime.json');
  for (let attempt = 0; attempt < 200; attempt += 1) {
    await delay(100);
    if (!existsSync(discovery)) continue;
    try {
      const record = JSON.parse(readFileSync(discovery, 'utf8'));
      if ((await fetch(`${record.url}/v1/health`)).ok) return { child, url: record.url };
    } catch { /* 等待 */ }
  }
  throw new Error('后台启动超时');
}

const workspace = mkdtempSync(path.join(os.tmpdir(), 'studio-release-run-'));
let backend = null;
try {
  backend = await startBackend(workspace);
  const pairingKey = JSON.parse(readFileSync(path.join(workspace, '.studio-service', 'credentials', 'pairing.key'), 'utf8')).pairingKey;
  const session = await fetch(`${backend.url}/v1/sessions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ pairingKey, clientName: 'release-runner', scopes: ['read', 'edit', 'preview', 'export'] }),
  }).then((response) => response.json());
  const token = session.token;

  const story = await buildStory(backend.url, token, workspace);
  const exported = await call(backend.url, token, 'POST', `/v1/projects/${story.projectId}/exports`, {});
  if (exported.status !== 201) throw new Error(`导出失败：${JSON.stringify(exported.body).slice(0, 300)}`);
  const body = exported.body;
  const packageDir = path.join(workspace, body.directory);
  console.log(`发布包：${path.relative(workspace, packageDir)}（${body.fileCount} 个文件，playerIncluded=${body.playerIncluded}）`);

  mkdirSync(outDir, { recursive: true });
  const reportFile = path.join(outDir, 'release-check.json');
  const check = await run(process.execPath, [path.join('scripts', 'studio-release-check.mjs'), '--package', packageDir, '--json', reportFile, '--run']);
  process.stdout.write(check.stdout);
  process.stderr.write(check.stderr);
  const report = JSON.parse(readFileSync(reportFile, 'utf8'));
  console.log(`发布检查：passed=${report.summary.passed} failed=${report.summary.failed} warnings=${report.summary.warnings}`);
  if (check.code !== 0) process.exitCode = 1;
} catch (error) {
  console.error(`发布组验证失败：${error.message}`);
  process.exitCode = 1;
} finally {
  backend?.child.kill('SIGKILL');
  await delay(300);
  if (!keepWorkspace) rmSync(workspace, { recursive: true, force: true });
  else console.log(`工作区保留：${workspace}`);
}
