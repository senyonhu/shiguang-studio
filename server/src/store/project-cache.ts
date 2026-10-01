// 工程内容缓存：用「文件大小 + 修改时间」的确定性指纹判断磁盘是否变化，命中时跳过全量读取与解析。
// 后台是正式工程的唯一写入者，每次提交后主动更新缓存；外部改动会被指纹变化检测到并重新读取。
import { statSync } from 'node:fs';
import path from 'node:path';
import type { StoryContent } from '../../../src/runtime/content.ts';
import { sha256Hex } from '../core/canonical.ts';
import type { FileSet, LoadedProject } from './doc.ts';
import { contentFileSet, loadProject } from './doc.ts';
import type { ProjectPaths } from './paths.ts';
import { listDirNames } from './fsread.ts';

export interface ProjectCacheEntry {
  generation: string;
  content: StoryContent;
  files: FileSet;
  loadedAt: string;
  hits: number;
}

export interface ProjectCacheStats {
  entries: number;
  hits: number;
  misses: number;
}

export class ProjectCache {
  private entries = new Map<string, ProjectCacheEntry>();
  private hits = 0;
  private misses = 0;

  private tokenFor(paths: ProjectPaths): string {
    const parts: string[] = [];
    const add = (abs: string, rel: string): void => {
      try {
        const stats = statSync(abs);
        parts.push(`${rel}:${stats.size}:${stats.mtimeMs}`);
      } catch {
        parts.push(`${rel}:missing`);
      }
    };
    add(paths.projectJson, 'project.json');
    add(paths.variablesJson, 'variables.json');
    add(paths.assetsManifest, 'assets/manifest.json');
    add(paths.themesFile, 'themes/theme.json');
    for (const [dir, prefix] of [[paths.chaptersDir, 'story/chapters'], [paths.scenesDir, 'story/scenes'], [paths.charactersDir, 'characters'], [paths.levelsDir, 'levels']] as const) {
      for (const name of listDirNames(dir)) add(path.join(dir, name), `${prefix}/${name}`);
    }
    parts.sort();
    return sha256Hex(parts.join('\n'));
  }

  /** 取得工程内容；命中缓存时只做一次 stat 指纹计算。 */
  load(paths: ProjectPaths): LoadedProject & { cached: boolean; generation: string } {
    const generation = this.tokenFor(paths);
    const entry = this.entries.get(paths.projectId);
    if (entry && entry.generation === generation) {
      this.hits += 1;
      entry.hits += 1;
      return { content: entry.content, files: entry.files, issues: [], cached: true, generation };
    }
    this.misses += 1;
    const loaded = loadProject(paths);
    this.entries.set(paths.projectId, { generation, content: loaded.content, files: loaded.files, loadedAt: new Date().toISOString(), hits: 0 });
    return { ...loaded, cached: false, generation };
  }

  /** 提交成功后用新内容更新缓存，避免立刻再读一遍磁盘。 */
  store(paths: ProjectPaths, content: StoryContent, files?: FileSet): void {
    const nextFiles = files ?? contentFileSet(content);
    this.entries.set(paths.projectId, {
      generation: this.tokenFor(paths),
      content,
      files: nextFiles,
      loadedAt: new Date().toISOString(),
      hits: 0,
    });
  }

  invalidate(projectId: string): void {
    this.entries.delete(projectId);
  }

  stats(): ProjectCacheStats {
    return { entries: this.entries.size, hits: this.hits, misses: this.misses };
  }
}
