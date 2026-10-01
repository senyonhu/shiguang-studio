// 发布打包：把工程内容与展示器打包成可独立运行的目录，并校验内容完整性与敏感信息。
// 发布包不包含 .studio 运行数据、凭据、制作 API 或编辑入口。
import { copyFileSync, existsSync, lstatSync, mkdirSync, readdirSync, readFileSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { StoryContent } from '../../../src/runtime/content.ts';
import { sha256Hex } from '../core/canonical.ts';
import { StudioError } from '../core/errors.ts';
import { loadProject, contentFileSet } from '../store/doc.ts';
import { projectPaths } from '../store/paths.ts';
import { DEFAULT_THEME } from '../../../src/runtime/content.ts';
import { API_VERSION, RUNTIME_KERNEL_VERSION } from '../version.ts';
import { copyDirectory, ensureDir } from '../store/fsx.ts';
import { installPlayerPortable } from './portable.ts';
import { PLAYER_SERVE_SCRIPT } from './player-server.ts';

export const PLAYER_DIR_NAME = 'dist-player';

export interface ExportResult {
  exportId: string;
  directory: string;
  revision: string;
  fileCount: number;
  totalBytes: number;
  contentDigest: string;
  playerIncluded: boolean;
  portableIncluded: boolean;
  sensitiveHits: string[];
  missingAssets: string[];
  missingLevels: string[];
  createdAt: string;
  serveCommand: string;
  playerUrl: string;
}

const SENSITIVE_PATTERNS = [
  /pairing\.key$/i,
  /sessions\.json$/i,
  /\.studio-service/i,
  /\.studio[\\/]/i,
  /credentials/i,
  /token/i,
];

function listFiles(dir: string, prefix = ''): { rel: string; size: number }[] {
  const out: { rel: string; size: number }[] = [];
  for (const name of readdirSync(dir, { withFileTypes: true })) {
    const rel = prefix ? `${prefix}/${name.name}` : name.name;
    const abs = path.join(dir, name.name);
    if (name.isDirectory()) out.push(...listFiles(abs, rel));
    else out.push({ rel, size: statSync(abs).size });
  }
  return out;
}

/** 內容清单服务使用的单文件内容包：只包含正式内容，不含运行数据与凭据。 */
export function buildContentBundle(content: StoryContent, revision: string, createdAt: string): Record<string, unknown> {
  return {
    apiVersion: API_VERSION,
    kernelVersion: RUNTIME_KERNEL_VERSION,
    projectId: content.project.projectId,
    revision,
    createdAt,
    project: content.project,
    chapters: content.chapters,
    scenes: content.scenes,
    variables: content.variables,
    assets: content.assets,
    characters: content.characters,
    levels: content.levels,
    theme: content.theme ?? DEFAULT_THEME,
  };
}

export interface ExportOptions {
  workspaceRoot: string;
  repoRoot: string;
  now?: string;
  portableRuntimeRoot?: string;
}

/** A registered level is playable only when its entry is an ordinary file inside levels/. */
export function missingLevelPrograms(root: string, content: StoryContent): string[] {
  return content.levels.filter((level) => {
    if (!level.entry.startsWith('levels/') || /[\\:\x00]/.test(level.entry) || level.entry.split('/').some((part) => !part || part === '..' || part.startsWith('.'))) return true;
    try {
      let entry = root;
      for (const segment of level.entry.split('/')) {
        entry = path.join(entry, segment);
        if (lstatSync(entry).isSymbolicLink()) return true;
      }
      const relative = path.relative(realpathSync(root), realpathSync(entry));
      return path.isAbsolute(relative) || relative === '..' || relative.startsWith(`..${path.sep}`) || !statSync(entry).isFile();
    } catch { return true; }
  }).map((level) => level.levelId);
}

export function assertLevelProgramsPresent(root: string, content: StoryContent): void {
  const missingLevels = missingLevelPrograms(root, content);
  if (missingLevels.length) throw new StudioError('VALIDATION_FAILED', '关卡程序尚未导入或入口文件缺失，请先导入关卡 HTML', {
    issues: missingLevels.map((levelId) => ({ code: 'LEVEL_PROGRAM_MISSING', objectId: levelId, field: 'entry', message: `关卡《${content.levels.find((level) => level.levelId === levelId)?.title ?? levelId}》的入口文件缺失或路径不安全` })),
    details: { missingLevels },
  });
}

export function exportProject(options: ExportOptions, projectId: string, state: { revision: string }): ExportResult {
  const paths = projectPaths(options.workspaceRoot, projectId);
  if (!existsSync(paths.projectJson)) throw new StudioError('NOT_FOUND', `工程 ${projectId} 不存在`);
  const createdAt = options.now ?? new Date().toISOString();
  const exportId = `exp-${createdAt.replace(/[:.]/g, '-')}`;
  const directory = path.join(paths.exportsDir, exportId);
  const { content } = loadProject(paths);
  assertLevelProgramsPresent(paths.root, content);
  const missingLevels: string[] = [];
  ensureDir(directory);
  const contentDir = path.join(directory, 'content');
  ensureDir(path.join(contentDir, 'assets', 'originals'));

  // 1. 内容（与后台工程一致的序列化结果 + 单文件内容包）
  //    防御性处理：正式内容里不应出现本机绝对路径；历史数据若已包含则在此脱敏并记录数量。
  const absolutePathPattern = /("(?:sourcePath|absPath|workspace|projectRoot)"\s*:\s*")([^"]*?)(\s*")/g;
  let pathRedactions = 0;
  const sanitize = (text: string): string => text.replace(absolutePathPattern, (_match, prefix: string, value: string, suffix: string) => {
    if (!/[\\/]/.test(value)) return `${prefix}${value}${suffix}`;
    pathRedactions += 1;
    return `${prefix}<redacted:${value.split(/[\\/]/).pop() ?? 'path'}>${suffix}`;
  });
  const files = contentFileSet(content);
  for (const [relPath, value] of files) {
    const target = path.join(contentDir, relPath.replace(/^assets\/manifest\.json$/, 'assets/manifest.json'));
    ensureDir(path.dirname(target));
    writeFileSync(target, sanitize(value as string), 'utf8');
  }
  writeFileSync(path.join(contentDir, 'content.json'), sanitize(`${JSON.stringify(buildContentBundle(content, state.revision, createdAt), null, 2)}\n`), 'utf8');

  // 2. 素材：只复制被工程登记的原始素材，并校验文件存在与散列一致
  const missingAssets: string[] = [];
  for (const asset of content.assets) {
    const source = path.join(paths.root, asset.path);
    if (!existsSync(source)) {
      missingAssets.push(asset.assetId);
      continue;
    }
    const target = path.join(contentDir, asset.path);
    ensureDir(path.dirname(target));
    copyFileSync(source, target);
    const digest = sha256Hex(readFileSync(target));
    if (digest !== asset.sha256) throw new StudioError('STORAGE_ERROR', `素材 ${asset.assetId} 内容散列与清单不一致，发布中止`);
  }

  // 3. 关卡程序及其子目录依赖；根目录 JSON 是模型定义，子目录 JSON 可能是关卡数据。
  const levelsSource = path.join(paths.root, 'levels');
  function copyLevelFiles(sourceDir: string, relative = ''): void {
    for (const entry of readdirSync(sourceDir, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) throw new StudioError('STORAGE_ERROR', '关卡依赖不能包含符号链接');
      const rel = relative ? `${relative}/${entry.name}` : entry.name;
      const source = path.join(sourceDir, entry.name);
      if (entry.isDirectory()) copyLevelFiles(source, rel);
      else if (entry.isFile() && (relative || !entry.name.endsWith('.json'))) {
        const target = path.join(contentDir, 'levels', rel);
        ensureDir(path.dirname(target));
        copyFileSync(source, target);
      }
    }
  }
  if (existsSync(levelsSource)) copyLevelFiles(levelsSource);

  // 4. 展示器构建产物（必须已经 npm run build:player）
  const playerSource = path.join(options.repoRoot, PLAYER_DIR_NAME);
  const playerIncluded = existsSync(path.join(playerSource, 'player.html'));
  if (playerIncluded) {
    copyDirectory(playerSource, directory);
  } else {
    writeFileSync(path.join(directory, 'PLAYER_MISSING.txt'), '未找到展示器构建产物（dist-player/player.html）。请先运行 npm run build:player 再导出。\n', 'utf8');
  }

  // 5. 本地运行脚本与发布清单
  writeFileSync(path.join(directory, 'serve.mjs'), PLAYER_SERVE_SCRIPT, 'utf8');
  const portableIncluded = playerIncluded && installPlayerPortable(
    directory,
    options.portableRuntimeRoot ?? path.resolve(options.repoRoot, '..', 'runtime'),
    path.join(options.repoRoot, 'scripts', 'studio-portable-launch.mjs'),
  );
  const fileList = listFiles(directory);
  const contentDigest = sha256Hex(fileList.map((file) => `${sha256Hex(readFileSync(path.join(directory, file.rel)))}  ${file.rel}`).sort().join('\n'));
  const sensitiveHits = fileList.map((file) => file.rel).filter((rel) => SENSITIVE_PATTERNS.some((pattern) => pattern.test(rel)));
  const manifest = {
    apiVersion: API_VERSION,
    kernelVersion: RUNTIME_KERNEL_VERSION,
    exportId,
    projectId,
    revision: state.revision,
    createdAt,
    playerIncluded,
    portableIncluded,
    fileCount: fileList.length,
    totalBytes: fileList.reduce((sum, file) => sum + file.size, 0),
    contentDigest,
    pathRedactions,
    sensitiveHits,
    missingAssets,
    missingLevels,
    files: fileList.map((file) => ({ rel: file.rel, size: file.size, sha256: sha256Hex(readFileSync(path.join(directory, file.rel))) })),
    notes: [
      '发布包不包含 .studio 运行数据、会话凭据、制作 API 与编辑入口。',
      '内容中的本机绝对路径已脱敏（见 pathRedactions 计数）。',
      '素材使用 assets/originals 下的原始文件；derived 播放副本尚未生成（未实现）。',
    ],
  };
  writeFileSync(path.join(directory, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');
  mkdirSync(paths.exportsDir, { recursive: true });

  return {
    exportId,
    directory: path.relative(options.workspaceRoot, directory).replace(/\\/g, '/'),
    revision: state.revision,
    fileCount: manifest.fileCount,
    totalBytes: manifest.totalBytes,
    contentDigest,
    playerIncluded,
    portableIncluded,
    sensitiveHits,
    missingAssets,
    missingLevels,
    createdAt,
    serveCommand: `node ${path.join(directory, 'serve.mjs')} --port 5199`,
    playerUrl: `http://127.0.0.1:5199/player.html`,
  };
}

export function listExports(workspaceRoot: string, projectId: string): { exportId: string; revision: string; createdAt: string; fileCount: number; sensitiveHits: string[] }[] {
  const paths = projectPaths(workspaceRoot, projectId);
  if (!existsSync(paths.exportsDir)) return [];
  const out: { exportId: string; revision: string; createdAt: string; fileCount: number; sensitiveHits: string[] }[] = [];
  for (const name of readdirSync(paths.exportsDir)) {
    const manifestPath = path.join(paths.exportsDir, name, 'manifest.json');
    if (!existsSync(manifestPath)) continue;
    try {
      const manifest = JSON.parse(readFileSync(manifestPath, 'utf8')) as { revision: string; createdAt: string; fileCount: number; sensitiveHits: string[] };
      out.push({ exportId: name, revision: manifest.revision, createdAt: manifest.createdAt, fileCount: manifest.fileCount, sensitiveHits: manifest.sensitiveHits ?? [] });
    } catch { /* 忽略损坏的发布包 */ }
  }
  return out.sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1));
}
