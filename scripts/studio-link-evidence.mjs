// 把某一轮证据包的结果回填到 requirements.json（evidence 引用与代码指纹）。
// 用法：node scripts/studio-link-evidence.mjs --run-id <runId>
// 说明：只写入已存在且通过的需求；未通过或未声明的需求保持原状态，不会被“补成通过”。
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { argValue, readJson, writeJson } from './studio-lib.mjs';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const runId = argValue(process.argv, '--run-id', '');
if (!runId) {
  console.error('需要 --run-id');
  process.exit(2);
}
const runDir = path.join(ROOT, 'artifacts', 'studio-review', runId);
const runJson = path.join(runDir, 'run.json');
if (!existsSync(runJson)) {
  console.error(`找不到证据包：${path.relative(ROOT, runJson)}`);
  process.exit(2);
}
const run = readJson(runJson);
const check = readJson(path.join(runDir, 'requirements-check.json'));
const byId = new Map(check.checks.map((item) => [item.id, item]));

const requirementPath = path.join(ROOT, 'review', 'studio', 'requirements.json');
const requirements = readJson(requirementPath);
let linked = 0;
let stale = 0;
for (const requirement of requirements.requirements) {
  const observed = byId.get(requirement.id);
  if (!observed) continue;
  if (observed.observed !== 'passed' || observed.declarationMismatch) {
    requirement.evidence = requirement.evidence ?? [];
    // 不覆盖为“通过”；仅记录本轮未能支撑该需求
    requirement.lastCheckedRun = { runId, observed: observed.observed, at: run.endedAt };
    stale += 1;
    continue;
  }
  const modes = new Set((requirement.tests ?? []).map((test) => test.mode));
  const artifacts = [];
  if (modes.has('unit') || modes.has('integration')) {
    artifacts.push(modes.has('unit') ? 'logs/quick.json' : 'logs/core.json');
  }
  if (modes.has('integration') && !artifacts.includes('logs/core.json')) artifacts.push('logs/core.json');
  if (modes.has('e2e_browser')) artifacts.push('logs/e2e.json');
  if (modes.has('fixture_provider')) artifacts.push('logs/media.json');
  if (modes.has('live_provider')) artifacts.push('logs/live-provider.json');
  requirement.evidence = [{ runId, artifacts: [...new Set(artifacts)], observed: 'passed', at: run.endedAt }];
  requirement.lastReviewedSourceDigest = run.sourceDigest;
  requirement.lastCheckedRun = { runId, observed: 'passed', at: run.endedAt };
  linked += 1;
}
requirements.lastEvidenceRun = {
  runId,
  sourceDigest: run.sourceDigest,
  startedAt: run.startedAt,
  endedAt: run.endedAt,
  summary: run.summary,
};
requirements.updatedAt = new Date().toISOString();
writeJson(requirementPath, requirements);
console.log(`linked=${linked} not_supported=${stale} runId=${runId} sourceDigest=${run.sourceDigest}`);
