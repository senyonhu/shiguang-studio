// 建立无 Git 仓库的第一方源码基线：路径/大小/SHA-256 清单 + 清单指纹。
// 用法：node scripts/studio-baseline.mjs [--out review/studio/baseline/inventory.json]
import { writeFileSync, mkdirSync, copyFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { EXCLUDED_DIR_NAMES, INCLUDE_DIRS, INCLUDE_ROOT_FILES, argValue, digestOf, scanSources, sha256File } from './studio-lib.mjs';

const root = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const out = path.resolve(root, argValue(process.argv, '--out', 'review/studio/baseline/inventory.json'));
const files = scanSources(root);
const inventory = {
  kind: 'studio-source-baseline',
  createdAt: new Date().toISOString(),
  projectRoot: root,
  vcs: 'none (无 .git 仓库，按交接约定建立路径/大小/SHA-256 基线)',
  scanScope: { includeDirs: INCLUDE_DIRS, includeRootFiles: INCLUDE_ROOT_FILES, maxFileBytes: 4 * 1024 * 1024 },
  exclusions: [...EXCLUDED_DIR_NAMES].sort(),
  exclusionReason: '依赖目录(node_modules/.venv)、SDK 工具链(tools)、编译输出(dist/release/test-results)、缓存(.runtime/.pytest_cache)、证据包(artifacts/review)、Ren’Py 大体积运行时(client)、存档(saves)',
  fileCount: files.length,
  totalBytes: files.reduce((n, f) => n + f.size, 0),
  digest: digestOf(files),
  files,
};
mkdirSync(path.dirname(out), { recursive: true });
writeFileSync(out, `${JSON.stringify(inventory, null, 2)}\n`, 'utf8');
console.log(`baseline files=${inventory.fileCount} bytes=${inventory.totalBytes}`);
console.log(`digest=${inventory.digest}`);
console.log(`out=${out}`);

// 保存即将被修改的既有文本文件原始版本，便于生成可比较差异。
const preserved = ['package.json', 'vitest.config.ts', 'vite.config.ts', 'tsconfig.json', 'README.md', 'TESTING.md', 'docs/studio-demo.md'];
const originalDir = path.join(path.dirname(out), 'original');
mkdirSync(originalDir, { recursive: true });
for (const rel of preserved) {
  const src = path.join(root, rel);
  if (!existsSync(src)) continue;
  const dest = path.join(originalDir, rel.replace(/[\\/]/g, '__'));
  copyFileSync(src, dest);
  console.log(`preserved ${rel} -> ${path.relative(root, dest)} sha256=${sha256File(src).slice(0, 16)}`);
}
