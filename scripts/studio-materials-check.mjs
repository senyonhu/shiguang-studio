// 材料一致性检查：确认 review/studio 里写的数字/指纹与冻结证据包里的实测结果一致。
// 存在的原因：本项目出现过两次“文档与实现漂移”（KI-032 能力描述、KI-033 覆盖口径），
// 因此让材料自己可被机器核对，而不是靠人眼看。
import { readFileSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(path.dirname(new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')), '..');
const reviewDir = path.join(ROOT, 'artifacts', 'studio-review');

const failures = [];
const notes = [];
const pass = (message) => notes.push(`[passed] ${message}`);
const fail = (message) => failures.push(message);

const handoff = readFileSync(path.join(ROOT, 'review', 'studio', 'HANDOFF.md'), 'utf8');

// 核对目标 = 材料里明确声称的那个证据包（而不是“最新”的包）：
// 材料与它所声称的运行必须一致；材料是否落后于最新运行由人审查，不在此处假装知道。
const claimedRunId = (handoff.match(/证据包`?\s*`?(studio-r\d+-[0-9-]+)/) ?? [])[1] ?? '';
if (!claimedRunId) { console.log('[failed] MATERIALS-CONSISTENCY: HANDOFF 里找不到它声称的证据包 runId'); process.exit(1); }
const claimedDir = path.join(reviewDir, claimedRunId);
let run;
try { run = JSON.parse(readFileSync(path.join(claimedDir, 'run.json'), 'utf8')); }
catch (error) { console.log(`[failed] MATERIALS-CONSISTENCY: 读不到材料声称的证据包 ${claimedRunId}：${error.message}`); process.exit(1); }
const latest = { dir: claimedDir, run };

// 1) HANDOFF 里写的指纹必须等于它声称的证据包的 sourceDigest
const digestInHandoff = (handoff.match(/`([0-9a-f]{64})`/) ?? [])[1] ?? '';
if (!digestInHandoff) fail('HANDOFF 里找不到 64 位源码指纹');
else if (digestInHandoff !== run.sourceDigest) fail(`HANDOFF 指纹 ${digestInHandoff} 与证据包 ${run.sourceDigest} 不一致`);
else pass(`HANDOFF 指纹与最新证据包一致（${run.sourceDigest.slice(0, 12)}…）`);

// 2) HANDOFF 里的“通过 N”必须等于证据包实际通过数
const passedCount = (run.results ?? []).filter((item) => item.status === 'passed').length;
const passInHandoff = Number((handoff.match(/通过\s*(\d+)\s*、失败/) ?? [])[1] ?? NaN);
if (!Number.isFinite(passInHandoff)) fail('HANDOFF 里找不到“通过 N、失败 N”统计');
else if (passInHandoff !== passedCount) fail(`HANDOFF 写通过 ${passInHandoff}，证据包实际 ${passedCount}`);
else pass(`HANDOFF 通过数与证据包一致（${passedCount}）`);

const failedCount = (run.results ?? []).filter((item) => item.status === 'failed').length;
const failInHandoff = Number((handoff.match(/失败\s*(\d+)/) ?? [])[1] ?? NaN);
if (Number.isFinite(failInHandoff) && failInHandoff !== failedCount) fail(`HANDOFF 写失败 ${failInHandoff}，证据包实际 ${failedCount}`);
else pass(`HANDOFF 失败数与证据包一致（${failedCount}）`);

// 3) 需求核对不得有“声明不一致”
try {
  const check = JSON.parse(readFileSync(path.join(latest.dir, 'requirements-check.json'), 'utf8'));
  const mismatches = (check.checks ?? []).filter((item) => item.declarationMismatch);
  if (mismatches.length) fail(`需求核对存在 ${mismatches.length} 处声明不一致：${mismatches.map((item) => item.id).join(', ')}`);
  else pass('需求核对无声明不一致');
  const notPassed = (check.checks ?? []).filter((item) => item.observed !== 'passed');
  // 未覆盖数必须与 HANDOFF 写明的一致（HANDOFF 会写“未覆盖 N 条”）
  const uncoveredInHandoff = Number((handoff.match(/未覆盖\s*\*{0,2}(\d+)/) ?? [])[1] ?? NaN);
  if (Number.isFinite(uncoveredInHandoff) && uncoveredInHandoff !== notPassed.length) {
    fail(`HANDOFF 写未覆盖 ${uncoveredInHandoff} 条，核对器实际 ${notPassed.length} 条（${notPassed.map((item) => item.id).join(', ')}）`);
  } else pass(`未覆盖需求数与 HANDOFF 一致（${notPassed.length}：${notPassed.map((item) => item.id).join(', ') || '无'}）`);
} catch (error) {
  fail(`读不到 requirements-check.json：${error.message}`);
}

// 4) 审查指引里写的数字必须与证据包一致
try {
  const guide = readFileSync(path.join(ROOT, 'review', 'studio', 'REVIEW-GUIDE.md'), 'utf8');
  const guideDigest = (guide.match(/([0-9a-f]{64})/) ?? [])[1] ?? '';
  if (!guideDigest) fail('审查指引里找不到源码指纹');
  else if (guideDigest !== run.sourceDigest) fail(`审查指引指纹 ${guideDigest} 与证据包不一致`);
  else pass('审查指引指纹与证据包一致');
  const guidePassed = Number((guide.match(/\*\*(\d+) 通过 \/ (\d+) 失败/) ?? [])[1] ?? NaN);
  if (!Number.isFinite(guidePassed)) fail('审查指引里找不到“N 通过 / N 失败”');
  else if (guidePassed !== passedCount) fail(`审查指引写通过 ${guidePassed}，证据包实际 ${passedCount}`);
  else pass(`审查指引通过数与证据包一致（${guidePassed}）`);
} catch (error) {
  fail(`读不到审查指引：${error.message}`);
}

// 5) 不应残留一次性脚本
const tmpScripts = readdirSync(path.join(ROOT, 'scripts')).filter((name) => name.startsWith('tmp-'));
if (tmpScripts.length) fail(`scripts/ 下残留一次性脚本：${tmpScripts.join(', ')}`);
else pass('scripts/ 下没有残留的一次性脚本');

for (const note of notes) console.log(note);
if (failures.length) {
  for (const message of failures) console.log(`[failed] MATERIALS-CONSISTENCY: ${message}`);
  console.log(`检查项：${notes.length + failures.length}，失败 ${failures.length}`);
  process.exit(1);
}
console.log(`检查项：${notes.length}，失败 0（最新证据包 ${path.basename(latest.dir)}）`);
