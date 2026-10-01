// 计算本轮源码指纹与方案散列，供证据包 run.json 使用。
// 用法：node scripts/studio-digest.mjs [--out <file>] [--manifest <file>]
import { writeFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { argValue, digestOf, scanSources, sha256File } from './studio-lib.mjs';

const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const files = scanSources(root);
const digest = digestOf(files);
const planDigest = sha256File(path.join(root, 'docs/editor-player-ai-plan.md'));
const promptDigest = sha256File(path.join(root, 'docs/implementation-agent-prompt.md'));
const contractDigest = sha256File(path.join(root, 'docs/review-handoff-contract.md'));
const value = {
  computedAt: new Date().toISOString(),
  projectRoot: root,
  sourceDigest: digest,
  fileCount: files.length,
  totalBytes: files.reduce((n, f) => n + f.size, 0),
  planDigest,
  promptDigest,
  contractDigest,
  note: '指纹覆盖第一方源码/配置/测试/设计文档与锁文件；排除依赖目录、工具链、编译输出、缓存、Ren’Py 运行时、以及 artifacts/ 与 review/ 证据目录本身。测试代码包含在指纹内。',
};
const manifestPath = argValue(process.argv, '--manifest');
if (manifestPath) {
  const abs = path.resolve(root, manifestPath);
  mkdirSync(path.dirname(abs), { recursive: true });
  writeFileSync(abs, `${JSON.stringify({ kind: 'studio-source-manifest', sourceDigest: digest, files }, null, 2)}\n`, 'utf8');
  value.digestManifest = path.relative(root, abs).replace(/\\/g, '/');
}
const out = argValue(process.argv, '--out');
if (out) {
  const abs = path.resolve(root, out);
  mkdirSync(path.dirname(abs), { recursive: true });
  writeFileSync(abs, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}
console.log(JSON.stringify(value, null, 2));
