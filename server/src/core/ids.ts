// 稳定对象 ID：前缀 + 16 位 base32（crypto 随机）。人工界面与 AI 工具使用同一套 ID 规则。
import { randomBytes } from 'node:crypto';

const ALPHABET = 'abcdefghijklmnopqrstuvwxyz0123456789';

export function makeId(prefix: string, length = 14): string {
  const bytes = randomBytes(length);
  let out = '';
  for (let i = 0; i < length; i += 1) out += ALPHABET[bytes[i] % ALPHABET.length];
  return `${prefix}_${out}`;
}

export const ID_PREFIX = {
  project: 'prj',
  chapter: 'ch',
  scene: 'sc',
  event: 'ev',
  asset: 'as',
  variable: 'var',
  character: 'chr',
  level: 'lv',
  theme: 'thm',
  job: 'job',
  trace: 'tr',
  session: 'sess',
} as const;

export const ID_PATTERN = /^[a-z]{2,4}_[a-z0-9]{4,32}$/;

export function isStudioId(value: unknown): value is string {
  return typeof value === 'string' && ID_PATTERN.test(value);
}

export function assertStudioId(value: unknown, field: string): string {
  if (!isStudioId(value)) throw new Error(`${field} 必须是稳定对象 ID（形如 prj_xxx），实际为 ${JSON.stringify(value)}`);
  return value;
}
