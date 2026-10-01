// 由实际差异生成 review/studio/changes.json 的基础字段，再合并人工补充的原因/符号/测试。
// 用法：node scripts/studio-changes.mjs
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { digestOf, readJson, scanSources, writeJson } from './studio-lib.mjs';

const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const baseline = readJson(path.join(root, 'review/studio/baseline/inventory.json'));
const reasonsPath = path.join(root, 'review/studio/changes-reasons.json');
const reasons = existsSync(reasonsPath) ? readJson(reasonsPath) : { entries: {}, defaults: {} };

const before = new Map(baseline.files.map((f) => [f.path, f]));
const current = scanSources(root);
const after = new Map(current.map((f) => [f.path, f]));

function describe(rel) {
  const exact = reasons.entries?.[rel];
  if (exact) return exact;
  let best = null;
  for (const [prefix, value] of Object.entries(reasons.entries ?? {})) {
    if (prefix.endsWith('/') && rel.startsWith(prefix) && (!best || prefix.length > best[0].length)) best = [prefix, value];
  }
  return best ? best[1] : (reasons.defaults ?? {});
}

const changes = [];
for (const [rel, file] of after) {
  const old = before.get(rel);
  if (old && old.sha256 === file.sha256) continue;
  const meta = describe(rel);
  changes.push({
    path: rel,
    change: old ? 'modified' : 'added',
    requirements: meta.requirements ?? [],
    reason: meta.reason ?? '(待补充原因)',
    symbols: meta.symbols ?? [],
    beforeSha256: old ? old.sha256 : null,
    afterSha256: file.sha256,
    beforeSize: old ? old.size : null,
    afterSize: file.size,
    tests: meta.tests ?? [],
  });
}
for (const [rel, old] of before) {
  if (after.has(rel)) continue;
  const meta = describe(rel);
  changes.push({
    path: rel,
    change: 'deleted',
    requirements: meta.requirements ?? [],
    reason: meta.reason ?? '(待补充原因)',
    symbols: meta.symbols ?? [],
    beforeSha256: old.sha256,
    afterSha256: null,
    beforeSize: old.size,
    afterSize: null,
    tests: meta.tests ?? [],
  });
}
changes.sort((a, b) => (a.path < b.path ? -1 : 1));

const out = {
  kind: 'studio-changes',
  generatedAt: new Date().toISOString(),
  vcs: baseline.vcs,
  baselineInventory: 'review/studio/baseline/inventory.json',
  baselineDigest: baseline.digest,
  baselineCreatedAt: baseline.createdAt,
  currentDigest: digestOf(current),
  note: 'change/before/after 字段由 scripts/studio-changes.mjs 扫描实际文件差异生成；requirements/reason/symbols/tests 来自 review/studio/changes-reasons.json（人工补充）。未列入本文件的文件在本轮未被改动。',
  userOriginalChanges: reasons.userOriginalChanges ?? [],
  counts: {
    added: changes.filter((c) => c.change === 'added').length,
    modified: changes.filter((c) => c.change === 'modified').length,
    deleted: changes.filter((c) => c.change === 'deleted').length,
  },
  changes,
};
writeJson(path.join(root, 'review/studio/changes.json'), out);
console.log(`changes added=${out.counts.added} modified=${out.counts.modified} deleted=${out.counts.deleted}`);
console.log(`baselineDigest=${out.baselineDigest}`);
console.log(`currentDigest=${out.currentDigest}`);

// 生成 diff 摘要（仅文本文件，便于审查者快速比较）
const diffDir = path.join(root, 'review/studio/baseline/diffs');
const { mkdirSync, writeFileSync } = await import('node:fs');
mkdirSync(diffDir, { recursive: true });
let count = 0;
for (const c of changes) {
  if (c.change !== 'modified') continue;
  const original = path.join(root, 'review/studio/baseline/original', c.path.replace(/[\\/]/g, '__'));
  if (!existsSync(original)) continue;
  const a = readFileSync(original, 'utf8').split('\n');
  const b = readFileSync(path.join(root, c.path), 'utf8').split('\n');
  writeFileSync(path.join(diffDir, `${c.path.replace(/[\\/]/g, '__')}.diff.txt`),
    `--- baseline/${c.path}\n+++ current/${c.path}\n--- baseline lines: ${a.length}\n+++ current lines: ${b.length}\n\n${a.map((l) => `- ${l}`).join('\n')}\n\n${b.map((l) => `+ ${l}`).join('\n')}\n`, 'utf8');
  count += 1;
}
console.log(`wrote ${count} baseline->current text snapshots to review/studio/baseline/diffs`);
