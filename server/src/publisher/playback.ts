// Temporary read-only playback links expose published content, never service credentials or workspace files.
import { createReadStream, existsSync, lstatSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, writeFileSync } from 'node:fs';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { randomBytes } from 'node:crypto';
import path from 'node:path';
import os from 'node:os';
import { sha256Hex } from '../core/canonical.ts';
import { StudioError } from '../core/errors.ts';
import { isStudioId } from '../core/ids.ts';
import type { StoryContent } from '../../../src/runtime/content.ts';
import { buildContentBundle, assertLevelProgramsPresent } from './service.ts';
import { projectPaths } from '../store/paths.ts';

const PUBLIC_EXTENSIONS = new Set(['.html', '.htm', '.js', '.mjs', '.css', '.json', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.wav', '.mp3', '.ogg', '.mp4', '.webm', '.woff', '.woff2', '.ttf', '.wasm', '.txt', '.csv']);
const MIME: Record<string, string> = { '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif', '.svg': 'image/svg+xml', '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg', '.mp4': 'video/mp4', '.webm': 'video/webm', '.woff': 'font/woff', '.woff2': 'font/woff2', '.wasm': 'application/wasm', '.txt': 'text/plain; charset=utf-8' };
export const PLAYBACK_TTL_MS = 30 * 60 * 1000;
interface Ticket { revision: string; expiresAt: number; bundle: Buffer; files: Map<string, { root: string; relative: string }> }

export function safePublicRelative(relative: string): boolean {
  return !!relative && !relative.includes('\\') && !relative.includes('\0') && !relative.startsWith('/') && !relative.includes(':') && relative.split('/').every((part) => !!part && part !== '..' && !part.startsWith('.'));
}

export function confinedFile(root: string, relative: string): string | null {
  if (!safePublicRelative(relative)) return null;
  const absoluteRoot = path.resolve(root);
  let current = absoluteRoot;
  try {
    if (lstatSync(current).isSymbolicLink()) return null;
    for (const segment of relative.split('/')) {
      current = path.join(current, segment);
      if (lstatSync(current).isSymbolicLink()) return null;
    }
    const realRoot = realpathSync(absoluteRoot);
    const real = realpathSync(current);
    const rel = path.relative(realRoot, real);
    if (path.isAbsolute(rel) || rel === '..' || rel.startsWith(`..${path.sep}`) || !statSync(real).isFile()) return null;
    return real;
  } catch { return null; }
}

function addTree(files: Ticket['files'], root: string, relative: string, publicPrefix = ''): void {
  if (!safePublicRelative(relative)) return;
  const directory = path.join(root, relative);
  if (!existsSync(directory) || lstatSync(directory).isSymbolicLink() || !lstatSync(directory).isDirectory()) return;
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.isSymbolicLink() || entry.name.startsWith('.')) continue;
    const child = `${relative}/${entry.name}`;
    if (entry.isDirectory()) addTree(files, root, child, publicPrefix);
    else if (entry.isFile() && PUBLIC_EXTENSIONS.has(path.extname(entry.name).toLowerCase()) && confinedFile(root, child)) {
      files.set(`${publicPrefix}${child}`, { root, relative: child });
    }
  }
}

export class PlaybackLinks {
  private readonly tickets = new Map<string, Ticket>();
  private readonly cachedFiles = new Map<string, number>();
  private cacheRoot: string | null = null;
  private readonly options: { workspaceRoot: string; repoRoot: string; now?: () => number; ttlMs?: number };
  constructor(options: { workspaceRoot: string; repoRoot: string; now?: () => number; ttlMs?: number }) { this.options = options; }
  private now(): number { return this.options.now?.() ?? Date.now(); }
  private release(ticket: Ticket): void {
    for (const file of ticket.files.values()) {
      const references = (this.cachedFiles.get(file.relative) ?? 1) - 1;
      if (references > 0) this.cachedFiles.set(file.relative, references);
      else { this.cachedFiles.delete(file.relative); rmSync(path.join(file.root, file.relative), { force: true }); }
    }
    if (!this.cachedFiles.size && this.cacheRoot) {
      const root = this.cacheRoot;
      if (path.dirname(root) !== path.resolve(os.tmpdir()) || !path.basename(root).startsWith('studio-playback-cache-')) throw new Error('Invalid playback cache directory');
      rmSync(root, { recursive: true, force: true }); this.cacheRoot = null;
    }
  }
  private pinFiles(originals: Ticket['files']): Ticket['files'] {
    this.cacheRoot ??= mkdtempSync(path.join(os.tmpdir(), 'studio-playback-cache-'));
    const files: Ticket['files'] = new Map();
    try {
      for (const [publicPath, source] of originals) {
        const absolute = confinedFile(source.root, source.relative);
        if (!absolute) throw new StudioError('VALIDATION_FAILED', '试玩文件已丢失，请重新打开试玩');
        const bytes = readFileSync(absolute);
        const relative = `${sha256Hex(bytes)}${path.extname(source.relative).toLowerCase()}`;
        if (!this.cachedFiles.has(relative)) writeFileSync(path.join(this.cacheRoot, relative), bytes, { flag: 'wx' });
        this.cachedFiles.set(relative, (this.cachedFiles.get(relative) ?? 0) + 1);
        files.set(publicPath, { root: this.cacheRoot, relative });
      }
      return files;
    } catch (error) { this.release({ files } as Ticket); throw error; }
  }
  private add(ticket: Omit<Ticket, 'expiresAt'>, origin: string): { url: string; revision: string; expiresAt: string } {
    const now = this.now();
    for (const [key, item] of this.tickets) if (item.expiresAt <= now) { this.release(item); this.tickets.delete(key); }
    if (this.tickets.size >= 100) { const first = this.tickets.keys().next().value!; this.release(this.tickets.get(first)!); this.tickets.delete(first); }
    const key = randomBytes(32).toString('hex');
    const expiresAt = now + (this.options.ttlMs ?? PLAYBACK_TTL_MS);
    this.tickets.set(key, { ...ticket, files: this.pinFiles(ticket.files), expiresAt });
    return { url: `${origin}/v1/playback/${key}/player.html`, revision: ticket.revision, expiresAt: new Date(expiresAt).toISOString() };
  }
  private playerFiles(root: string): Ticket['files'] {
    if (!confinedFile(root, 'player.html')) throw new StudioError('RENDER_UNAVAILABLE', '展示器尚未安装，请完整解压编辑器程序后重试');
    const files: Ticket['files'] = new Map([['player.html', { root, relative: 'player.html' }]]);
    addTree(files, root, 'assets');
    return files;
  }
  project(origin: string, projectId: string, snapshot: { content: StoryContent; revision: string }, sceneId?: string): { url: string; revision: string; expiresAt: string } {
    if (!isStudioId(projectId) || !projectId.startsWith('prj_')) throw new StudioError('BAD_REQUEST', '工程 ID 不正确');
    if (sceneId && !snapshot.content.scenes.some((scene) => scene.sceneId === sceneId)) throw new StudioError('NOT_FOUND', '预览场景不存在');
    const root = projectPaths(this.options.workspaceRoot, projectId).root;
    assertLevelProgramsPresent(root, snapshot.content);
    const files = this.playerFiles(path.join(this.options.repoRoot, 'dist-player'));
    for (const asset of snapshot.content.assets) {
      if (!asset.path.startsWith('assets/') || !confinedFile(root, asset.path)) throw new StudioError('VALIDATION_FAILED', `素材《${asset.name}》的文件不存在或路径不安全`);
      files.set(`content/${asset.path}`, { root, relative: asset.path });
    }
    addTree(files, root, 'levels', 'content/');
    const content = { ...snapshot.content, project: { ...snapshot.content.project, ...(sceneId ? { entrySceneId: sceneId } : {}) } };
    const bundle = Buffer.from(JSON.stringify(buildContentBundle(content, snapshot.revision, new Date(this.now()).toISOString())));
    return this.add({ files, bundle, revision: snapshot.revision }, origin);
  }
  exported(origin: string, projectId: string, exportId: string): { url: string; revision: string; expiresAt: string } {
    if (!isStudioId(projectId) || !projectId.startsWith('prj_') || !/^exp-[A-Za-z0-9_-]{1,100}$/.test(exportId)) throw new StudioError('BAD_REQUEST', '导出记录不正确');
    const exportRoot = projectPaths(this.options.workspaceRoot, projectId).exportsDir;
    const manifest = confinedFile(exportRoot, `${exportId}/manifest.json`);
    const bundleFile = confinedFile(exportRoot, `${exportId}/content/content.json`);
    if (!manifest || !bundleFile) throw new StudioError('NOT_FOUND', '导出的作品不存在');
    const record = JSON.parse(readFileSync(manifest, 'utf8')) as { revision?: string; projectId?: string };
    if (record.projectId !== projectId || typeof record.revision !== 'string') throw new StudioError('VALIDATION_FAILED', '导出记录不完整');
    const root = path.join(exportRoot, exportId);
    const files = this.playerFiles(root);
    addTree(files, root, 'content/assets');
    addTree(files, root, 'content/levels');
    return this.add({ files, bundle: readFileSync(bundleFile), revision: record.revision }, origin);
  }
  serve(req: IncomingMessage, res: ServerResponse, pathname: string): void {
    if (!['GET', 'HEAD'].includes(req.method ?? '')) throw new StudioError('FORBIDDEN', '播放链接只允许读取');
    let decoded: string;
    try { decoded = decodeURIComponent(pathname); } catch { throw new StudioError('BAD_REQUEST', '播放路径不正确'); }
    const match = /^\/v1\/playback\/([a-f0-9]{64})\/(.+)$/.exec(decoded);
    if (!match || !safePublicRelative(match[2])) throw new StudioError('NOT_FOUND', '播放文件不存在');
    const ticket = this.tickets.get(match[1]);
    if (!ticket || ticket.expiresAt <= this.now()) {
      if (ticket) { this.release(ticket); this.tickets.delete(match[1]); }
      throw new StudioError('UNAUTHENTICATED', '播放链接已过期，请回到编辑器重新打开');
    }
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Referrer-Policy', 'no-referrer');
    res.setHeader('X-Content-Type-Options', 'nosniff');
    if (match[2] === 'content/content.json') {
      res.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'content-length': ticket.bundle.length });
      res.end(req.method === 'HEAD' ? undefined : ticket.bundle);
      return;
    }
    const file = ticket.files.get(match[2]);
    const absolute = file ? confinedFile(file.root, file.relative) : null;
    if (!absolute) throw new StudioError('NOT_FOUND', '播放文件不存在');
    const size = statSync(absolute).size;
    let start = 0; let end = size - 1; let status = 200;
    const headers: Record<string, string | number> = { 'content-type': MIME[path.extname(absolute).toLowerCase()] ?? 'application/octet-stream', 'accept-ranges': 'bytes' };
    if (req.headers.range) {
      const range = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range);
      if (!range || (!range[1] && !range[2])) { res.writeHead(416, { 'content-range': `bytes */${size}` }); res.end(); return; }
      if (!range[1]) start = Math.max(0, size - Number(range[2]));
      else { start = Number(range[1]); if (range[2]) end = Math.min(end, Number(range[2])); }
      if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end || start >= size) { res.writeHead(416, { 'content-range': `bytes */${size}` }); res.end(); return; }
      status = 206; headers['content-range'] = `bytes ${start}-${end}/${size}`;
    }
    headers['content-length'] = size ? end - start + 1 : 0;
    res.writeHead(status, headers);
    if (req.method === 'HEAD' || !size) res.end();
    else createReadStream(absolute, { start, end }).on('error', () => res.destroy()).pipe(res);
  }
  clear(): void { for (const ticket of this.tickets.values()) this.release(ticket); this.tickets.clear(); }
}
