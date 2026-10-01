// 只读文件系统辅助：工具适配进程只允许读（会话凭据、发现记录、配置），不允许写工程文件。
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

export function exists(abs: string): boolean {
  return existsSync(abs);
}

export function readText(abs: string): string {
  return readFileSync(abs, 'utf8');
}

export function readJson<T>(abs: string): T {
  return JSON.parse(readFileSync(abs, 'utf8')) as T;
}

export function readBinary(abs: string): Buffer {
  return readFileSync(abs);
}

export function fileSize(abs: string): number {
  return statSync(abs).size;
}

export function listDirNames(abs: string): string[] {
  if (!existsSync(abs)) return [];
  return readdirSync(abs).sort();
}

export function readJsonIfExists<T>(abs: string): T | null {
  if (!existsSync(abs)) return null;
  try { return readJson<T>(abs); } catch { return null; }
}

export function relativeTo(root: string, abs: string): string {
  return path.relative(root, abs).replace(/\\/g, '/');
}
