// 拾光 Studio 交接材料的公共扫描/散列工具。
// 本文件被 studio-baseline.mjs、studio-digest.mjs、studio-changes.mjs 共用，
// 保证基线、源码指纹和改动清单使用同一套扫描口径。
import { createHash } from 'node:crypto';
import { readdirSync, statSync, readFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';

// 第一方源码/配置/测试/设计文档的扫描范围（相对项目根）。
export const INCLUDE_DIRS = ['src', 'server', 'tests', 'scripts', 'docs', 'story', 'backend', 'infra', 'packaging', 'public'];
// 根目录下直接参与构建/验证的文件。
export const INCLUDE_ROOT_FILES = [
  'package.json', 'package-lock.json', 'tsconfig.json', 'vitest.config.ts', 'vite.config.ts', 'vite.player.config.ts', 'vite.studio.config.ts',
  'playwright.config.ts', 'pytest.ini', 'index.html', 'studio.html', 'studio-live.html', 'player.html',
  'README.md', 'TESTING.md', '启动剧情工作台.cmd', '启动网页版Demo.cmd', '启动Demo.cmd', '启动RenPy新版.cmd',
  'start-studio.ps1', 'start.ps1', 'start-renpy.ps1', 'repair-docker-runtime.ps1',
];
// 明确排除：依赖、SDK/工具链、编译输出、缓存、大体积运行时、证据包与旧工程产物。
export const EXCLUDED_DIR_NAMES = new Set([
  'node_modules', '__pycache__', '.pytest_cache', 'dist', 'test-results', '.runtime', '.venv',
  'tools', 'release', 'artifacts', 'review', '.git', 'saves', 'cache', 'screenshots',
]);
export const MAX_FILE_BYTES = 4 * 1024 * 1024;

export function sha256(buf) {
  return createHash('sha256').update(buf).digest('hex');
}

export function sha256File(abs) {
  return sha256(readFileSync(abs));
}

function walk(root, dir, out) {
  let entries;
  try { entries = readdirSync(path.join(root, dir), { withFileTypes: true }); } catch { return; }
  for (const entry of entries.sort((a, b) => (a.name < b.name ? -1 : 1))) {
    const rel = dir ? `${dir}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      if (EXCLUDED_DIR_NAMES.has(entry.name)) continue;
      walk(root, rel, out);
      continue;
    }
    if (!entry.isFile()) continue;
    const abs = path.join(root, rel);
    let st;
    try { st = statSync(abs); } catch { continue; }
    if (st.size > MAX_FILE_BYTES) continue;
    out.push({ path: rel, size: st.size, sha256: sha256File(abs) });
  }
}

/** 返回按路径排序的 {path,size,sha256} 清单。 */
export function scanSources(root) {
  const out = [];
  for (const dir of INCLUDE_DIRS) {
    if (existsSync(path.join(root, dir))) walk(root, dir, out);
  }
  for (const name of INCLUDE_ROOT_FILES) {
    const abs = path.join(root, name);
    if (!existsSync(abs)) continue;
    const st = statSync(abs);
    if (st.size > MAX_FILE_BYTES) continue;
    out.push({ path: name, size: st.size, sha256: sha256File(abs) });
  }
  return out.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
}

/** 对清单计算确定性指纹：每行 "sha256  size  path"。 */
export function digestOf(entries) {
  const lines = entries.map((e) => `${e.sha256}  ${e.size}  ${e.path}`).join('\n');
  return sha256(Buffer.from(lines, 'utf8'));
}

export function writeJson(abs, value) {
  mkdirSync(path.dirname(abs), { recursive: true });
  writeFileSync(abs, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

export function readJson(abs) {
  return JSON.parse(readFileSync(abs, 'utf8'));
}

export function argValue(argv, flag, fallback = null) {
  const i = argv.indexOf(flag);
  return i >= 0 && i + 1 < argv.length ? argv[i + 1] : fallback;
}
