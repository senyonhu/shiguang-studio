import { spawn } from 'node:child_process';
import path from 'node:path';
import { isStudioId } from '../core/ids.ts';
import { StudioError } from '../core/errors.ts';
import { projectPaths } from '../store/paths.ts';
import { confinedFile } from './playback.ts';

export function exportDirectory(workspaceRoot: string, projectId: string, exportId: string): string {
  if (!isStudioId(projectId) || !projectId.startsWith('prj_') || !/^exp-[A-Za-z0-9_-]{1,100}$/.test(exportId)) throw new StudioError('BAD_REQUEST', '导出记录不正确');
  const root = projectPaths(workspaceRoot, projectId).exportsDir;
  if (!confinedFile(root, `${exportId}/manifest.json`) || !confinedFile(root, `${exportId}/content/content.json`)) throw new StudioError('NOT_FOUND', '导出的作品不存在');
  return path.resolve(root, exportId);
}

export async function openExportDirectory(workspaceRoot: string, projectId: string, exportId: string): Promise<{ opened: true }> {
  const directory = exportDirectory(workspaceRoot, projectId, exportId);
  if (process.platform !== 'win32') throw new StudioError('UNSUPPORTED_CAPABILITY', '此设备暂不支持直接打开文件夹');
  await new Promise<void>((resolve, reject) => {
    const child = spawn('explorer.exe', [directory], { windowsHide: true, detached: true, stdio: 'ignore' });
    child.once('error', () => reject(new StudioError('BACKEND_UNAVAILABLE', '无法打开文件夹，请从导出路径查找作品')));
    child.once('spawn', () => { child.unref(); resolve(); });
  });
  return { opened: true };
}
