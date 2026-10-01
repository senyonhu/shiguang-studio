// 文件系统写入原语：所有正式工程写入都经过这里的“临时文件 + fsync + 原子替换”。
// 只读辅助在 fsread.ts；工具适配进程只引用 fsread，避免把写能力带进适配层。
import { closeSync, copyFileSync, existsSync, fsyncSync, mkdirSync, openSync, readFileSync, readdirSync, renameSync, rmSync, unlinkSync, writeSync } from 'node:fs';
import path from 'node:path';
import { readJsonIfExists } from './fsread.ts';

export { exists, fileSize, listDirNames, readBinary, readJson, readJsonIfExists, readText, relativeTo } from './fsread.ts';

export function ensureDir(abs: string): void {
  mkdirSync(abs, { recursive: true });
}

/** 逐项复制，兼容 Windows 中文目录；拒绝符号链接，不跟随到目录外。 */
export function copyDirectory(from: string, to: string): void {
  ensureDir(to);
  for (const entry of readdirSync(from, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) throw new Error(`不能复制符号链接：${entry.name}`);
    const source = path.join(from, entry.name);
    const target = path.join(to, entry.name);
    if (entry.isDirectory()) copyDirectory(source, target);
    else if (entry.isFile()) copyFileSync(source, target);
  }
}

/** 目录 fsync：保证 rename 结果落盘（Windows 上打开目录会失败，忽略）。 */
export function fsyncDir(abs: string): void {
  let fd: number | null = null;
  try {
    fd = openSync(abs, 'r');
    fsyncSync(fd);
  } catch {
    // Windows 不允许以只读方式 fsync 目录，忽略。
  } finally {
    if (fd !== null) {
      try { closeSync(fd); } catch { /* ignore */ }
    }
  }
}

/** 原子写入：写 <target>.tmp-<nonce> → fsync → rename。 */
export function writeFileDurable(abs: string, data: string | Buffer, nonce = 'tmp'): void {
  ensureDir(path.dirname(abs));
  const tmp = `${abs}.tmp-${nonce}`;
  const fd = openSync(tmp, 'w');
  try {
    if (typeof data === 'string') writeSync(fd, data);
    else writeSync(fd, data);
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  renameSync(tmp, abs);
  fsyncDir(path.dirname(abs));
}

export function writeTextIfChanged(abs: string, data: string, nonce = 'tmp'): boolean {
  if (existsSync(abs)) {
    try {
      if (readFileSync(abs, 'utf8') === data) return false;
    } catch { /* 重新写入 */ }
  }
  writeFileDurable(abs, data, nonce);
  return true;
}

export function copyFileDurable(from: string, to: string, nonce = 'tmp'): void {
  ensureDir(path.dirname(to));
  const tmp = `${to}.tmp-${nonce}`;
  copyFileSync(from, tmp);
  // Windows 上 FlushFileBuffers 需要写权限，因此用 r+ 打开后再 fsync。
  const fd = openSync(tmp, 'r+');
  try { fsyncSync(fd); } finally { closeSync(fd); }
  renameSync(tmp, to);
  fsyncDir(path.dirname(to));
}

export function removeFileIfExists(abs: string): void {
  try { unlinkSync(abs); } catch { /* 不存在即忽略 */ }
}

export function removeDirRecursive(abs: string): void {
  rmSync(abs, { recursive: true, force: true });
}

export function writeJsonDurable(abs: string, value: unknown, nonce = 'tmp'): void {
  writeFileDurable(abs, `${JSON.stringify(value, null, 2)}\n`, nonce);
}

/** 把相对路径转成安全的文件名（用于 journal 备份目录）。 */
export function safeRelName(relPath: string): string {
  return relPath.replace(/[\\/]/g, '__');
}

export { readJsonIfExists as readJsonIfExistsCompat };
