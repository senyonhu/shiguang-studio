// 工作区注册表与工程创建/列举。
// 工程创建也走 journal 提交，避免出现“半个工程”。
import path from 'node:path';
import { existsSync } from 'node:fs';
import type { StoryContent } from '../../../src/runtime/content.ts';
import { PROJECT_FORMAT_VERSION, RUNTIME_KERNEL_VERSION } from '../version.ts';
import { StudioError } from '../core/errors.ts';
import { ID_PREFIX, makeId } from '../core/ids.ts';
import type { ProjectRegistryEntry } from './registry-types.ts';
import { contentFileSet, diffFileSets, emptyContent, ensureProjectDirs, loadProject } from './doc.ts';
import { commitTransaction, emptyState, loadState } from './journal.ts';
import { projectPaths, workspacePaths } from './paths.ts';
import { ensureDir, readJsonIfExists, writeJsonDurable } from './fsx.ts';

export interface ProjectRegistry {
  apiVersion: string;
  projects: ProjectRegistryEntry[];
}

export function readRegistry(workspaceRoot: string): ProjectRegistry {
  const file = workspacePaths(workspaceRoot).projectsJson;
  const registry = readJsonIfExists<ProjectRegistry>(file);
  if (registry && Array.isArray(registry.projects)) return registry;
  return { apiVersion: '1', projects: [] };
}

export function writeRegistry(workspaceRoot: string, registry: ProjectRegistry): void {
  ensureDir(workspaceRoot);
  writeJsonDurable(workspacePaths(workspaceRoot).projectsJson, registry, 'registry');
}

export interface ProjectSummary extends ProjectRegistryEntry {
  revision: string;
  chapterCount: number;
  sceneCount: number;
  eventCount: number;
  assetCount: number;
  formatVersion: number;
}

export function summarizeProject(workspaceRoot: string, projectId: string): ProjectSummary {
  const paths = projectPaths(workspaceRoot, projectId);
  const { content } = loadProject(paths);
  const state = loadState(paths, projectId);
  const registry = readRegistry(workspaceRoot);
  const entry = registry.projects.find((item) => item.projectId === projectId);
  return {
    projectId,
    title: content.project.title,
    createdAt: entry?.createdAt ?? content.project.createdAt,
    updatedAt: content.project.updatedAt,
    registeredAt: entry?.registeredAt ?? content.project.createdAt,
    revision: state.revision,
    chapterCount: content.chapters.length,
    sceneCount: content.scenes.length,
    eventCount: content.scenes.reduce((count, scene) => count + scene.events.length, 0),
    assetCount: content.assets.length,
    formatVersion: content.project.formatVersion,
  };
}

export function listProjects(workspaceRoot: string): ProjectSummary[] {
  const registry = readRegistry(workspaceRoot);
  const found = new Map<string, string>();
  for (const entry of registry.projects) found.set(entry.projectId, entry.projectId);
  const summaries: ProjectSummary[] = [];
  for (const projectId of found.keys()) {
    if (!existsSync(projectPaths(workspaceRoot, projectId).projectJson)) continue;
    try {
      summaries.push(summarizeProject(workspaceRoot, projectId));
    } catch {
      // 损坏的工程在列表中跳过，由校验接口单独报告
    }
  }
  return summaries.sort((a, b) => (a.createdAt < b.createdAt ? -1 : 1));
}

export interface CreateProjectInput {
  title: string;
  projectId?: string;
  chapterTitle?: string;
  sceneTitle?: string;
  now?: string;
}

export interface CreateProjectResult {
  projectId: string;
  revision: string;
  chapterId: string;
  sceneId: string;
  summary: ProjectSummary;
}

export function createProject(workspaceRoot: string, input: CreateProjectInput): CreateProjectResult {
  const now = input.now ?? new Date().toISOString();
  if (!input.title || !input.title.trim() || input.title.length > 120) {
    throw new StudioError('VALIDATION_FAILED', '工程标题必须是 1–120 字', { issues: [{ code: 'INVALID_TITLE', message: '工程标题必须是 1–120 字' }] });
  }
  const projectId = input.projectId ?? makeId(ID_PREFIX.project);
  const paths = projectPaths(workspaceRoot, projectId);
  if (existsSync(paths.projectJson)) throw new StudioError('PROJECT_EXISTS', `工程 ${projectId} 已存在`);
  ensureProjectDirs(paths);

  const chapterId = makeId(ID_PREFIX.chapter);
  const sceneId = makeId(ID_PREFIX.scene);
  const content: StoryContent = emptyContent(projectId, input.title.trim(), now);
  content.project.formatVersion = PROJECT_FORMAT_VERSION;
  content.project.runtimeCompat = `studio-runtime/${RUNTIME_KERNEL_VERSION}`;
  content.project.entrySceneId = sceneId;
  content.chapters = [{ chapterId, title: input.chapterTitle?.trim() || '第一章', order: 0, sceneIds: [sceneId], updatedAt: now }];
  content.scenes = [{ sceneId, chapterId, title: input.sceneTitle?.trim() || '开场', events: [], updatedAt: now }];
  content.project.chapterIds = [chapterId];

  const contentChanges = diffFileSets(new Map(), contentFileSet(content));
  const state = emptyState(projectId);
  const transactionId = makeId('txn');
  commitTransaction(paths, {
    transactionId,
    requestId: `create-${projectId}`,
    state,
    history: {
      transactionId,
      requestId: `create-${projectId}`,
      sequence: 0,
      revisionBefore: '0',
      revisionAfter: '0',
      actor: 'service:create-project',
      sessionId: 'service',
      operations: [{ action: 'project.create', targetIds: [projectId], payload: { title: content.project.title, chapterId, sceneId } }],
      inverseOperations: [],
      changedIds: [projectId, chapterId, sceneId],
      summary: `创建工程《${content.project.title}》`,
      at: now,
    },
    contentChanges,
  });

  const registry = readRegistry(workspaceRoot);
  if (!registry.projects.some((entry) => entry.projectId === projectId)) {
    registry.projects.push({ projectId, title: content.project.title, createdAt: now, updatedAt: now, registeredAt: now });
    writeRegistry(workspaceRoot, registry);
  }
  return { projectId, revision: state.revision, chapterId, sceneId, summary: summarizeProject(workspaceRoot, projectId) };
}

export function touchRegistryTitle(workspaceRoot: string, projectId: string, title: string, updatedAt: string): void {
  const registry = readRegistry(workspaceRoot);
  const entry = registry.projects.find((item) => item.projectId === projectId);
  if (!entry) return;
  entry.title = title;
  entry.updatedAt = updatedAt;
  writeRegistry(workspaceRoot, registry);
}

export function projectRoot(workspaceRoot: string, projectId: string): string {
  return path.join(workspaceRoot, projectId);
}
