// 工作区配置：预先授权的素材导入根目录等。AI 不能提交任意系统路径。
import path from 'node:path';
import { existsSync } from 'node:fs';
import { ensureDir, readJsonIfExists, writeJsonDurable } from './fsx.ts';
import { workspacePaths } from './paths.ts';

export interface ServiceConfig {
  apiVersion: string;
  importRoots: string[];
  /** 单个生成任务预算（分/点由适配器声明），0 表示未设置 */
  mediaBudgetLimit: number;
  mediaConcurrency: number;
  notes?: string[];
}

export function configPath(workspaceRoot: string): string {
  return path.join(workspacePaths(workspaceRoot).serviceDir, 'config.json');
}

export function defaultConfig(workspaceRoot: string): ServiceConfig {
  return {
    apiVersion: '1',
    importRoots: [path.join(workspaceRoot, 'imports')],
    mediaBudgetLimit: 0,
    mediaConcurrency: 1,
    notes: ['importRoots 是素材导入的唯一来源范围；未列入的路径一律拒绝。默认包含 <workspace>/imports。'],
  };
}

export function loadConfig(workspaceRoot: string): ServiceConfig {
  const file = configPath(workspaceRoot);
  const existing = readJsonIfExists<ServiceConfig>(file);
  const config = existing ?? defaultConfig(workspaceRoot);
  if (!Array.isArray(config.importRoots)) config.importRoots = defaultConfig(workspaceRoot).importRoots;
  if (typeof config.mediaBudgetLimit !== 'number') config.mediaBudgetLimit = 0;
  if (typeof config.mediaConcurrency !== 'number') config.mediaConcurrency = 1;
  if (!existing) {
    ensureDir(path.dirname(file));
    writeJsonDurable(file, config, 'init');
  }
  return config;
}

export function addImportRoot(workspaceRoot: string, root: string): ServiceConfig {
  const config = loadConfig(workspaceRoot);
  const resolved = path.resolve(root);
  if (!config.importRoots.includes(resolved)) config.importRoots.push(resolved);
  writeJsonDurable(configPath(workspaceRoot), config, 'update');
  return config;
}

export function importsDirReady(workspaceRoot: string): void {
  const dir = path.join(workspaceRoot, 'imports');
  if (!existsSync(dir)) ensureDir(dir);
}
