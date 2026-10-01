// 验证证据包生成器：运行验证分组、收集原始日志/轨迹、写入 run.json 与 artifacts.sha256。
// 用法：
//   node scripts/studio-evidence.mjs [--run-id <id>] [--groups quick,core] [--e2e] [--label <说明>]
// 说明：runId 目录不会覆盖旧结果；每组命令、退出码、耗时与原始输出都保留在 logs/ 下。
import { spawn } from 'node:child_process';
import { appendFileSync, existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { argValue, sha256, sha256File } from './studio-lib.mjs';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const argv = process.argv.slice(2);
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const runId = argValue(argv, '--run-id', `studio-p0-${stamp}`);
const label = argValue(argv, '--label', '');
const groupsArg = argValue(argv, '--groups', 'quick,core');
const withE2e = argv.includes('--e2e');
const runDir = path.join(ROOT, 'artifacts', 'studio-review', runId);
const logsDir = path.join(runDir, 'logs');
const tracesDir = path.join(runDir, 'traces');
const outputsDir = path.join(runDir, 'outputs');
for (const dir of [logsDir, tracesDir, outputsDir]) mkdirSync(dir, { recursive: true });

const startedAt = new Date().toISOString();
const commands = [];
const results = [];
const limitations = [];

function run(command, args, options = {}) {
  return new Promise((resolve) => {
    const start = Date.now();
    const child = spawn(command, args, {
      cwd: ROOT,
      env: { ...process.env, ...(options.env ?? {}) },
      // 只有 npx 这类需要 shell 解析的命令才走 shell；直接用 node 执行时不能用 shell，
      // 否则含空格的 node.exe 路径会被拆断。
      shell: options.shell === true,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
    child.on('close', (code) => resolve({ code, stdout, stderr, ms: Date.now() - start }));
    child.on('error', (error) => resolve({ code: -1, stdout, stderr: `${stderr}\n${error.message}`, ms: Date.now() - start }));
  });
}

function writeLog(name, text) {
  const abs = path.join(logsDir, name);
  writeFileSync(abs, text, 'utf8');
  return path.relative(ROOT, abs).replace(/\\/g, '/');
}

function readJsonSafe(abs) {
  try { return JSON.parse(readFileSync(abs, 'utf8')); } catch { return null; }
}

function parseVitestJson(abs) {
  const data = readJsonSafe(abs);
  if (!data || !Array.isArray(data.testResults)) return [];
  const entries = [];
  for (const file of data.testResults) {
    const filePath = path.relative(ROOT, file.name ?? '').replace(/\\/g, '/');
    for (const assertion of file.assertionResults ?? []) {
      entries.push({
        file: filePath,
        name: [...(assertion.ancestorTitles ?? []), assertion.title].join(' › '),
        status: assertion.status === 'passed' ? 'passed' : assertion.status === 'failed' ? 'failed' : 'skipped',
        durationMs: Math.round(assertion.duration ?? 0),
        failureMessages: assertion.failureMessages ?? [],
      });
    }
  }
  return entries;
}

// 1. 源码指纹与方案散列
const digestRun = await run(process.execPath, ['scripts/studio-digest.mjs', '--out', path.join('artifacts', 'studio-review', runId, 'logs', 'source-digest.json'), '--manifest', path.join('artifacts', 'studio-review', runId, 'logs', 'source-manifest.json')]);
const digest = readJsonSafe(path.join(logsDir, 'source-digest.json'));
commands.push({ command: 'node scripts/studio-digest.mjs', groups: ['digest'], exitCode: digestRun.code, durationMs: digestRun.ms });
if (!digest) {
  limitations.push('源码指纹计算失败，run.json 中的 sourceDigest 为空');
}

// 2. 验证分组
const GROUP_COMMANDS = {
  quick: { args: ['vitest', 'run', 'tests/protocol.test.ts', 'tests/studio.test.ts', 'tests/studio/unit', '--reporter=default', '--reporter=json', `--outputFile=${path.join(logsDir, 'quick.json')}`], description: '协议、类型与关键单元行为' },
  core: { args: ['vitest', 'run', 'tests/studio/integration', '--reporter=default', '--reporter=json', `--outputFile=${path.join(logsDir, 'core.json')}`], description: '真实后台进程、MCP 工具调用、持久化、重启、冲突、幂等、权限与恢复' },
};
const requested = groupsArg.split(',').map((item) => item.trim()).filter(Boolean);

for (const group of requested) {
  if (['plugin', 'release', 'performance'].includes(group)) continue; // 这三个分组在后面单独执行
  const spec = GROUP_COMMANDS[group];
  if (!spec) {
    results.push({ group, status: 'not_implemented', note: `未实现的验证分组：${group}` });
    limitations.push(`验证分组 ${group} 尚未实现`);
    continue;
  }
  const exec = await run('npx', spec.args, { env: { STUDIO_EVIDENCE_DIR: runDir }, shell: true });
  const logPath = writeLog(`${group}.log`, `$ npx ${spec.args.join(' ')}\n\n[stdout]\n${exec.stdout}\n[stderr]\n${exec.stderr}`);
  const cases = parseVitestJson(path.join(logsDir, `${group}.json`));
  commands.push({ command: `npx ${spec.args.slice(0, 2).join(' ')} ${spec.args.slice(2).filter((a) => !a.startsWith('--')).join(' ')}`, group, description: spec.description, exitCode: exec.code, durationMs: exec.ms, log: logPath });
  for (const item of cases) {
    results.push({ group, ...item, mode: group === 'core' ? 'integration' : 'unit' });
  }
  if (!cases.length) {
    results.push({ group, status: exec.code === 0 ? 'no_tests_collected' : 'failed', note: `分组 ${group} 未解析到用例结果（退出码 ${exec.code}）` });
  }
}

// 3. 插件分组：打包 + 结构检查（静态可验证部分；宿主加载不在本机验证范围内）
if (requested.includes('plugin')) {
  const build = await run(process.execPath, ['scripts/build-plugin.mjs']);
  const buildLog = writeLog('plugin-build.log', `$ node scripts/build-plugin.mjs\n\n[stdout]\n${build.stdout}\n[stderr]\n${build.stderr}`);
  commands.push({ command: 'node scripts/build-plugin.mjs', group: 'plugin', description: '打包 MCP 工具适配器到插件目录', exitCode: build.code, durationMs: build.ms, log: buildLog });
  results.push({ group: 'plugin', name: 'build:plugin 打包成功', status: build.code === 0 ? 'passed' : 'failed', durationMs: build.ms, mode: 'static_check' });

  const reportFile = path.join(outputsDir, 'plugin-check.json');
  const check = await run(process.execPath, ['scripts/studio-plugin-check.mjs', '--json', reportFile]);
  const checkLog = writeLog('plugin-check.log', `$ node scripts/studio-plugin-check.mjs\n\n[stdout]\n${check.stdout}\n[stderr]\n${check.stderr}`);
  commands.push({ command: 'node scripts/studio-plugin-check.mjs --json outputs/plugin-check.json', group: 'plugin', description: '插件包结构检查', exitCode: check.code, durationMs: check.ms, log: checkLog });
  const report = readJsonSafe(reportFile);
  if (report?.checks) {
    for (const item of report.checks) {
      results.push({
        group: 'plugin',
        name: `${item.id}: ${item.message}`,
        status: item.status === 'passed' ? 'passed' : item.status === 'warning' ? 'warning' : 'failed',
        durationMs: 0,
        mode: 'static_check',
      });
    }
    for (const note of report.notVerifiedHere ?? []) limitations.push(`插件：${note}`);
  } else {
    results.push({ group: 'plugin', status: check.code === 0 ? 'no_report' : 'failed', note: '未能解析插件检查报告' });
  }
}

// 4. 浏览器验证（人工界面）
if (withE2e) {
  const e2eJson = path.join(logsDir, 'e2e.json');
  const healthy = await fetch('http://127.0.0.1:5173/studio-live.html').then((response) => response.ok).catch(() => false);
  if (!healthy) {
    results.push({ group: 'ui', status: 'not_run', note: '开发服务器 127.0.0.1:5173 未运行，浏览器验证未执行' });
    limitations.push('浏览器验证需要先运行 npm run dev（127.0.0.1:5173）');
  } else {
    const exec = await run('npx', ['playwright', 'test', 'tests/e2e/studio-live.spec.ts', 'tests/e2e/p2-player.spec.ts', '--reporter=list', '--reporter=json'], { env: { PLAYWRIGHT_JSON_OUTPUT_NAME: e2eJson }, shell: true });
    const logPath = writeLog('e2e.log', `$ npx playwright test tests/e2e/studio-live.spec.ts tests/e2e/p2-player.spec.ts\n\n[stdout]\n${exec.stdout}\n[stderr]\n${exec.stderr}`);
    commands.push({ command: 'npx playwright test tests/e2e/studio-live.spec.ts tests/e2e/p2-player.spec.ts', group: 'ui', description: '人工编辑器读取后台工程与草稿冲突、独立展示器运行发布包', exitCode: exec.code, durationMs: exec.ms, log: logPath });
    const parsed = readJsonSafe(e2eJson);
    if (parsed?.suites) {
      const walk = (suite) => {
        for (const spec of suite.specs ?? []) {
          for (const test of spec.tests ?? []) {
            const outcome = (test.results ?? [])[0]?.status ?? 'unknown';
            results.push({
              group: 'ui',
              file: String(spec.file ?? '').replace(/\\/g, '/'),
              name: spec.title,
              status: outcome === 'passed' ? 'passed' : outcome === 'skipped' ? 'skipped' : 'failed',
              durationMs: Math.round((test.results ?? [])[0]?.duration ?? 0),
              mode: 'e2e_browser',
            });
          }
        }
        for (const child of suite.suites ?? []) walk(child);
      };
      walk(parsed);
    } else {
      results.push({ group: 'ui', status: exec.code === 0 ? 'passed' : 'failed', note: 'Playwright JSON 报告未解析，见 logs/e2e.log' });
    }
  }
}

// 4. 发布组：导出发布包并做验收检查（完整性、素材、敏感信息、实际启动）
if (requested.includes('release')) {
  const exec = await run(process.execPath, ['scripts/studio-release-run.mjs', '--out', outputsDir], { env: { STUDIO_EVIDENCE_DIR: runDir } });
  const logPath = writeLog('release.log', `$ node scripts/studio-release-run.mjs --out ${path.relative(ROOT, outputsDir)}\n\n[stdout]\n${exec.stdout}\n[stderr]\n${exec.stderr}`);
  commands.push({ command: 'node scripts/studio-release-run.mjs --out outputs', group: 'release', description: '发布包导出与验收检查（含启动冒烟）', exitCode: exec.code, durationMs: exec.ms, log: logPath });
  const report = readJsonSafe(path.join(runDir, 'outputs', 'release-check.json'));
  if (report?.checks) {
    for (const item of report.checks) {
      results.push({ group: 'release', name: `${item.id}: ${item.message}`, status: item.status === 'passed' ? 'passed' : item.status === 'warning' ? 'warning' : 'failed', durationMs: 0, mode: 'release_check' });
    }
  } else {
    results.push({ group: 'release', status: exec.code === 0 ? 'no_report' : 'failed', note: '未能解析发布检查报告，见 logs/release.log' });
  }
}

// 5. 性能组：按指定规模测量（默认 mid；target 规模需要更长时间，见报告 notMeasured）
if (requested.includes('performance')) {
  const profile = argValue(argv, '--scale-profile', 'mid');
  const exec = await run(process.execPath, ['scripts/studio-scale.mjs', '--profile', profile, '--json', path.join(runDir, 'outputs', `scale-${profile}.json`)]);
  const logPath = writeLog('performance.log', `$ node scripts/studio-scale.mjs --profile ${profile}\n\n[stdout]\n${exec.stdout}\n[stderr]\n${exec.stderr}`);
  commands.push({ command: `node scripts/studio-scale.mjs --profile ${profile}`, group: 'performance', description: '规模生成与方案 11 指标测量', exitCode: exec.code, durationMs: exec.ms, log: logPath });
  const report = readJsonSafe(path.join(runDir, 'outputs', `scale-${profile}.json`));
  if (report?.results) {
    for (const item of report.results) {
      results.push({
        group: 'performance',
        name: `${item.id}（实测 ${item.measuredMs}${item.unit} / 目标 ${item.target}${item.unit}；${item.note}）`,
        status: item.pass === null ? 'not_measured' : item.pass ? 'passed' : 'failed',
        durationMs: item.measuredMs,
        mode: 'performance',
      });
    }
    for (const note of report.notMeasured ?? []) limitations.push(`性能组：${note}`);
    results.push({ group: 'performance', name: `规模信息：${JSON.stringify(report.scale)}`, status: 'info', durationMs: 0, mode: 'performance' });
  } else {
    results.push({ group: 'performance', status: exec.code === 0 ? 'no_report' : 'failed', note: '未能解析性能报告，见 logs/performance.log' });
  }
}

// 6. 需求交叉核对：requirements.json 中声明的测试必须在本轮真实出现
const requirementsPath = path.join(ROOT, 'review', 'studio', 'requirements.json');
const requirements = readJsonSafe(requirementsPath);
const requirementChecks = [];
if (requirements?.requirements) {
  for (const requirement of requirements.requirements) {
    const declared = requirement.tests ?? [];
    const observed = [];
    for (const test of declared) {
      // Playwright 报告里 file 只有文件名，vitest 报告里是相对路径，因此两侧互相包含即算匹配。
      const fileMatches = (itemFile, testFile) => !testFile || !itemFile || itemFile.includes(testFile) || testFile.endsWith(itemFile);
      // 结构检查（打包、文档守卫等脚本化检查）没有测试文件，结果名形如「PLUGIN-XXX: 说明」：
      // mode=plugin_check 的声明按「检查 id + 说明」匹配阶段结果（修复 KI-033）。
      const match = results.find((item) => {
        if (typeof item.name !== 'string' || !test.name) return false;
        if (test.mode === 'plugin_check') return item.name.startsWith(`${test.name}:`) || item.name.includes(test.name);
        return item.name.includes(test.name) && fileMatches(item.file ?? '', test.file ?? '');
      });
      observed.push({ name: test.name, file: test.file ?? null, mode: test.mode ?? null, found: !!match, status: match?.status ?? 'not_found' });
    }
    const allFound = observed.length > 0 && observed.every((item) => item.found);
    const allPassed = allFound && observed.every((item) => item.status === 'passed');
    const observedStatus = allPassed ? 'passed' : allFound ? 'failed' : observed.length ? 'test_not_found' : 'no_tests_declared';
    requirementChecks.push({
      id: requirement.id,
      declaredImplementationStatus: requirement.implementationStatus,
      declaredVerificationStatus: requirement.verificationStatus,
      observed: observedStatus,
      // 人工声明的“已验证”必须在本轮结果里有对应通过用例，否则明确标出不一致。
      declarationMismatch: requirement.verificationStatus === 'passed' && observedStatus !== 'passed',
      tests: observed,
    });
  }
} else {
  limitations.push('未找到 review/studio/requirements.json，跳过需求交叉核对');
}
const requirementCheckPath = path.join(runDir, 'requirements-check.json');
writeFileSync(requirementCheckPath, `${JSON.stringify({ runId, sourceDigest: digest?.sourceDigest ?? null, checks: requirementChecks }, null, 2)}\n`, 'utf8');

// 6. run.json
const passed = results.filter((item) => item.status === 'passed').length;
const failed = results.filter((item) => item.status === 'failed').length;
const skipped = results.filter((item) => item.status === 'skipped').length;
const other = results.length - passed - failed - skipped;
const runRecord = {
  runId,
  label,
  startedAt,
  endedAt: new Date().toISOString(),
  sourceDigest: digest?.sourceDigest ?? null,
  digestManifest: 'logs/source-manifest.json',
  planDigest: digest?.planDigest ?? null,
  promptDigest: digest?.promptDigest ?? null,
  contractDigest: digest?.contractDigest ?? null,
  fileCount: digest?.fileCount ?? null,
  environment: {
    platform: `${os.platform()} ${os.release()}`,
    arch: os.arch(),
    node: process.version,
    cpus: os.cpus().length,
    cpuModel: os.cpus()[0]?.model ?? '',
    totalMemoryMB: Math.round(os.totalmem() / 1024 / 1024),
    playwrightChannel: 'msedge',
  },
  workingDirectory: ROOT,
  commands,
  summary: { passed, failed, skipped, other, total: results.length },
  results,
  requirementChecks: 'requirements-check.json',
  evidence: {
    logs: readdirSync(logsDir).map((name) => `logs/${name}`),
    traces: existsSync(tracesDir) ? readdirSync(tracesDir).map((name) => `traces/${name}`) : [],
    outputs: existsSync(outputsDir) ? readdirSync(outputsDir).map((name) => `outputs/${name}`) : [],
  },
  limitations,
  notes: [
    'core 分组的后台、MCP 与恢复用例都在独立临时工作区运行，结束后删除测试工程。',
    'traces/ 中的请求轨迹已脱敏（令牌、配对密钥不落盘）。',
    'requirements-check.json 是脚本对 requirements.json 声明的交叉核对结果，不改写人工声明的状态。',
  ],
};
writeFileSync(path.join(runDir, 'run.json'), `${JSON.stringify(runRecord, null, 2)}\n`, 'utf8');

// 7. artifacts.sha256（清单自身不纳入散列）
const lines = [];
const walk = (dir, prefix = '') => {
  for (const name of readdirSync(dir).sort()) {
    const abs = path.join(dir, name);
    const rel = prefix ? `${prefix}/${name}` : name;
    if (statSync(abs).isDirectory()) walk(abs, rel);
    else if (rel !== 'artifacts.sha256') lines.push(`${sha256File(abs)}  ${rel}`);
  }
};
walk(runDir);
writeFileSync(path.join(runDir, 'artifacts.sha256'), `${lines.join('\n')}\n`, 'utf8');

appendFileSync(path.join(ROOT, 'artifacts', 'studio-review', 'index.jsonl'), `${JSON.stringify({ runId, at: runRecord.endedAt, sourceDigest: runRecord.sourceDigest, summary: runRecord.summary, label })}\n`, 'utf8');

console.log(`runId=${runId}`);
console.log(`dir=${path.relative(ROOT, runDir)}`);
console.log(`sourceDigest=${runRecord.sourceDigest}`);
console.log(`passed=${passed} failed=${failed} skipped=${skipped} other=${other}`);
console.log(`artifacts.sha256 entries=${lines.length} (self digest ${sha256(readFileSync(path.join(runDir, 'artifacts.sha256'))).slice(0, 16)})`);
if (failed > 0) process.exitCode = 1;
