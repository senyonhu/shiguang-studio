// 工作区与工程目录布局（对应方案 6.1 建议的工程目录）。
import path from 'node:path';

export interface WorkspacePaths {
  root: string;
  projectsJson: string;
  serviceDir: string;
  runtimeJson: string;
  credentialsDir: string;
  pairingKeyFile: string;
  pairingMetaFile: string;
  sessionsFile: string;
  lockFile: string;
  logsDir: string;
}

export interface ProjectPaths {
  projectId: string;
  root: string;
  projectJson: string;
  chaptersDir: string;
  scenesDir: string;
  charactersDir: string;
  variablesJson: string;
  assetsDir: string;
  assetsManifest: string;
  originalsDir: string;
  derivedDir: string;
  levelsDir: string;
  themesDir: string;
  themesFile: string;
  recipesDir: string;
  testsDir: string;
  studioDir: string;
  stateJson: string;
  historyDir: string;
  journalDir: string;
  jobsDir: string;
  snapshotsDir: string;
  exportsDir: string;
  generationDir: string;
}

export function workspacePaths(root: string): WorkspacePaths {
  const serviceDir = path.join(root, '.studio-service');
  const credentialsDir = path.join(serviceDir, 'credentials');
  return {
    root,
    projectsJson: path.join(root, 'projects.json'),
    serviceDir,
    runtimeJson: path.join(serviceDir, 'runtime.json'),
    credentialsDir,
    pairingKeyFile: path.join(credentialsDir, 'pairing.key'),
    pairingMetaFile: path.join(credentialsDir, 'pairing.json'),
    sessionsFile: path.join(credentialsDir, 'sessions.json'),
    lockFile: path.join(serviceDir, 'service.lock'),
    logsDir: path.join(serviceDir, 'logs'),
  };
}

export function projectPaths(workspaceRoot: string, projectId: string): ProjectPaths {
  const root = path.join(workspaceRoot, projectId);
  const studio = path.join(root, '.studio');
  return {
    projectId,
    root,
    projectJson: path.join(root, 'project.json'),
    chaptersDir: path.join(root, 'story', 'chapters'),
    scenesDir: path.join(root, 'story', 'scenes'),
    charactersDir: path.join(root, 'characters'),
    variablesJson: path.join(root, 'variables.json'),
    assetsDir: path.join(root, 'assets'),
    assetsManifest: path.join(root, 'assets', 'manifest.json'),
    originalsDir: path.join(root, 'assets', 'originals'),
    derivedDir: path.join(root, 'assets', 'derived'),
    levelsDir: path.join(root, 'levels'),
    themesDir: path.join(root, 'themes'),
    themesFile: path.join(root, 'themes', 'theme.json'),
    recipesDir: path.join(root, 'generation', 'recipes'),
    testsDir: path.join(root, 'tests', 'scenarios'),
    generationDir: path.join(root, 'generation'),
    studioDir: studio,
    stateJson: path.join(studio, 'state.json'),
    historyDir: path.join(studio, 'history'),
    journalDir: path.join(studio, 'journal'),
    jobsDir: path.join(studio, 'jobs'),
    snapshotsDir: path.join(studio, 'snapshots'),
    exportsDir: path.join(root, 'exports'),
  };
}
