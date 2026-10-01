// 规范化 JSON 与散列：幂等判定、内容散列、证据指纹共用同一实现。
import { createHash } from 'node:crypto';

export function sha256Hex(input: string | Buffer): string {
  return createHash('sha256').update(input).digest('hex');
}

/** 稳定序列化：对象键排序，便于比较内容是否相同。 */
export function canonicalize(value: unknown): string {
  return JSON.stringify(normalize(value));
}

function normalize(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(normalize);
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      const v = (value as Record<string, unknown>)[key];
      if (v === undefined) continue;
      out[key] = normalize(v);
    }
    return out;
  }
  return value;
}

export function contentHash(value: unknown): string {
  return sha256Hex(canonicalize(value));
}

export function prettyJson(value: unknown): string {
  return `${JSON.stringify(value, null, 2)}\n`;
}
