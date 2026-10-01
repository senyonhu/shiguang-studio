// 素材产物校验：只根据文件头解析类型与基本规格，文件确实可解析才登记为素材。
// 这是“可解码才算可用”的本地判定，不代表已完成播放渲染验证。
import { sha256Hex } from '../core/canonical.ts';
import { deflateSync } from 'node:zlib';
import type { AssetKind } from '../../../src/runtime/events.ts';

export interface DecodeOk {
  ok: true;
  mimeType: string;
  extension: string;
  format: string;
  sha256: string;
  width?: number;
  height?: number;
  durationSeconds?: number;
  notes: string[];
}

export interface DecodeFail { ok: false; message: string }

export const MAX_ASSET_BYTES = 512 * 1024 * 1024;

export function guessMimeType(name: string): string {
  const ext = name.toLowerCase().slice(name.lastIndexOf('.'));
  switch (ext) {
    case '.png': return 'image/png';
    case '.jpg':
    case '.jpeg': return 'image/jpeg';
    case '.webp': return 'image/webp';
    case '.gif': return 'image/gif';
    case '.wav': return 'audio/wav';
    case '.mp3': return 'audio/mpeg';
    case '.ogg': return 'audio/ogg';
    case '.mp4': return 'video/mp4';
    case '.webm': return 'video/webm';
    case '.txt': return 'text/plain';
    case '.json': return 'application/json';
    default: return 'application/octet-stream';
  }
}

const has = (buf: Buffer, offset: number, bytes: number[]): boolean =>
  buf.length >= offset + bytes.length && bytes.every((byte, index) => buf[offset + index] === byte);

type Parsed = DecodeOk | DecodeFail | null;

function parsePng(buf: Buffer): Parsed {
  if (!has(buf, 0, [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])) return null;
  if (buf.length < 33 || buf.toString('ascii', 12, 16) !== 'IHDR') return { ok: false, message: 'PNG 缺少 IHDR 块' };
  const width = buf.readUInt32BE(16);
  const height = buf.readUInt32BE(20);
  if (!width || !height) return { ok: false, message: 'PNG 尺寸不合法' };
  return { ok: true, mimeType: 'image/png', extension: '.png', format: 'png', sha256: sha256Hex(buf), width, height, notes: ['解析 IHDR 尺寸'] };
}

function parseJpeg(buf: Buffer): Parsed {
  if (!has(buf, 0, [0xff, 0xd8, 0xff])) return null;
  let offset = 2;
  while (offset + 9 < buf.length) {
    if (buf[offset] !== 0xff) { offset += 1; continue; }
    const marker = buf[offset + 1];
    const length = buf.readUInt16BE(offset + 2);
    if (marker >= 0xc0 && marker <= 0xcf && marker !== 0xc4 && marker !== 0xc8 && marker !== 0xcc) {
      const height = buf.readUInt16BE(offset + 5);
      const width = buf.readUInt16BE(offset + 7);
      return { ok: true, mimeType: 'image/jpeg', extension: '.jpg', format: 'jpeg', sha256: sha256Hex(buf), width, height, notes: ['解析 SOF 尺寸'] };
    }
    offset += 2 + length;
  }
  return { ok: false, message: 'JPEG 未找到 SOF 尺寸信息' };
}

function parseGif(buf: Buffer): Parsed {
  if (!has(buf, 0, [0x47, 0x49, 0x46, 0x38])) return null;
  if (buf.length < 10) return { ok: false, message: 'GIF 文件头不完整' };
  return { ok: true, mimeType: 'image/gif', extension: '.gif', format: 'gif', sha256: sha256Hex(buf), width: buf.readUInt16LE(6), height: buf.readUInt16LE(8), notes: ['解析逻辑屏幕尺寸'] };
}

function parseWebp(buf: Buffer): Parsed {
  if (!(has(buf, 0, [0x52, 0x49, 0x46, 0x46]) && has(buf, 8, [0x57, 0x45, 0x42, 0x50]))) return null;
  const chunk = buf.toString('ascii', 12, 16);
  if (chunk === 'VP8X' && buf.length >= 30) {
    const width = 1 + (buf[24] | (buf[25] << 8) | (buf[26] << 16));
    const height = 1 + (buf[27] | (buf[28] << 8) | (buf[29] << 16));
    return { ok: true, mimeType: 'image/webp', extension: '.webp', format: 'webp', sha256: sha256Hex(buf), width, height, notes: ['解析 VP8X 画布尺寸'] };
  }
  return { ok: true, mimeType: 'image/webp', extension: '.webp', format: 'webp', sha256: sha256Hex(buf), notes: [`WebP 块 ${chunk}（未解析尺寸）`] };
}

function parseWav(buf: Buffer): Parsed {
  if (!(has(buf, 0, [0x52, 0x49, 0x46, 0x46]) && has(buf, 8, [0x57, 0x41, 0x56, 0x45]))) return null;
  let offset = 12;
  let byteRate = 0;
  let dataSize = 0;
  while (offset + 8 <= buf.length) {
    const id = buf.toString('ascii', offset, offset + 4);
    const size = buf.readUInt32LE(offset + 4);
    if (id === 'fmt ') byteRate = buf.readUInt32LE(offset + 16);
    if (id === 'data') { dataSize = size; break; }
    offset += 8 + size + (size % 2);
  }
  if (!byteRate || !dataSize) return { ok: false, message: 'WAV 缺少 fmt/data 块' };
  return { ok: true, mimeType: 'audio/wav', extension: '.wav', format: 'wav', sha256: sha256Hex(buf), durationSeconds: Number((dataSize / byteRate).toFixed(3)), notes: ['按 fmt.byteRate 估算时长'] };
}

function parseMp4(buf: Buffer): Parsed {
  if (buf.length < 16 || buf.toString('ascii', 4, 8) !== 'ftyp') return null;
  let offset = 0;
  let durationSeconds: number | undefined;
  while (offset + 8 <= buf.length) {
    const size = buf.readUInt32BE(offset);
    const type = buf.toString('ascii', offset + 4, offset + 8);
    if (size < 8) break;
    if (type === 'mvhd' && offset + 32 <= buf.length) {
      const version = buf[offset + 8];
      const timescale = version === 1 ? buf.readUInt32BE(offset + 28) : buf.readUInt32BE(offset + 20);
      const duration = version === 1 ? Number(buf.readBigUInt64BE(offset + 32)) : buf.readUInt32BE(offset + 24);
      if (timescale) durationSeconds = Number((duration / timescale).toFixed(3));
    }
    offset += size;
  }
  return { ok: true, mimeType: 'video/mp4', extension: '.mp4', format: 'mp4', sha256: sha256Hex(buf), durationSeconds, notes: durationSeconds === undefined ? ['未找到 mvhd 时长'] : ['解析 mvhd 时长'] };
}

function parseWebm(buf: Buffer): Parsed {
  if (!has(buf, 0, [0x1a, 0x45, 0xdf, 0xa3])) return null;
  return { ok: true, mimeType: 'video/webm', extension: '.webm', format: 'webm', sha256: sha256Hex(buf), notes: ['EBML 头匹配（未解析轨道细节）'] };
}

function parseMp3(buf: Buffer): Parsed {
  if (!(has(buf, 0, [0x49, 0x44, 0x33]) || (buf.length > 1 && buf[0] === 0xff && (buf[1] & 0xe0) === 0xe0))) return null;
  return { ok: true, mimeType: 'audio/mpeg', extension: '.mp3', format: 'mp3', sha256: sha256Hex(buf), notes: ['MP3 帧头/ID3 匹配（未解析时长）'] };
}

export function decodeAsset(bytes: Buffer, kind: AssetKind): DecodeOk | DecodeFail {
  if (!bytes.length) return { ok: false, message: '文件为空' };
  if (bytes.length > MAX_ASSET_BYTES) return { ok: false, message: `文件超过 ${Math.round(MAX_ASSET_BYTES / 1024 / 1024)} MB 上限` };
  for (const parser of [parsePng, parseJpeg, parseGif, parseWebp, parseWav, parseMp4, parseWebm, parseMp3]) {
    const result = parser(bytes);
    if (result) {
      if (!result.ok) return result;
      if (!kindCompatible(kind, result.format)) return { ok: false, message: `格式 ${result.format} 与素材类型 ${kind} 不匹配` };
      return result;
    }
  }
  return { ok: false, message: '无法识别的文件格式（支持 PNG/JPEG/GIF/WebP/WAV/MP3/MP4/WebM）' };
}

export function kindCompatible(kind: AssetKind, format: string): boolean {
  const images = ['png', 'jpeg', 'gif', 'webp'];
  const audio = ['wav', 'mp3', 'ogg'];
  const video = ['mp4', 'webm'];
  if (kind === 'audio') return audio.includes(format);
  if (kind === 'video') return video.includes(format);
  return images.includes(format);
}

/** 生成一个最小合法 PNG（测试适配器与故障注入使用），避免在测试里伪造外部服务返回。 */
export function encodeSolidPng(width: number, height: number, rgb: [number, number, number]): Buffer {
  const raw = Buffer.alloc((width * 3 + 1) * height);
  for (let y = 0; y < height; y += 1) {
    raw[y * (width * 3 + 1)] = 0;
    for (let x = 0; x < width; x += 1) {
      const offset = y * (width * 3 + 1) + 1 + x * 3;
      raw[offset] = rgb[0];
      raw[offset + 1] = rgb[1];
      raw[offset + 2] = rgb[2];
    }
  }
  const idat = deflateSync(raw);
  const chunk = (type: string, data: Buffer): Buffer => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const typeAndData = Buffer.concat([Buffer.from(type, 'ascii'), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(typeAndData) >>> 0);
    return Buffer.concat([length, typeAndData, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', idat),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

let crcTable: number[] | null = null;
function crc32(buf: Buffer): number {
  if (!crcTable) {
    crcTable = [];
    for (let n = 0; n < 256; n += 1) {
      let c = n;
      for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      crcTable[n] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (const byte of buf) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}
