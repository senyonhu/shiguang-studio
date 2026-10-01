// 编辑器与展示器共用的本机启动器。只能通过包内 Node 执行，无 npm/全局运行时依赖。
import { createServer } from 'node:http';
import { createReadStream, copyFileSync, existsSync, mkdirSync, openSync, closeSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { randomBytes } from 'node:crypto';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const config = JSON.parse(readFileSync(path.join(here, 'portable.json'), 'utf8'));
const dataDir = path.join(here, 'data');
mkdirSync(dataDir, { recursive: true });
const stateFile = path.join(dataDir, 'launcher-state.json');
const lockFile = path.join(dataDir, 'startup.lock');
const preferencesFile = path.join(dataDir, 'preferences.json');
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
function copyTree(from, to) {
  mkdirSync(to, { recursive: true });
  for (const entry of readdirSync(from, { withFileTypes: true })) {
    if (entry.isSymbolicLink()) throw new Error('示例不能包含符号链接');
    if (entry.isDirectory()) copyTree(path.join(from, entry.name), path.join(to, entry.name));
    else if (entry.isFile()) copyFileSync(path.join(from, entry.name), path.join(to, entry.name));
  }
}
function json(file) { try { return JSON.parse(readFileSync(file, 'utf8')); } catch { return null; } }
function alive(pid) { try { process.kill(pid, 0); return true; } catch { return false; } }
function localState(state) {
  if (!state?.secret || !Number.isInteger(state.pid)) return false;
  try { const url = new URL(state.origin); return url.protocol === 'http:' && url.hostname === '127.0.0.1' && url.pathname === '/'; } catch { return false; }
}
async function probe(state) {
  if (!localState(state)) return false;
  try {
    const response = await fetch(`${state.origin}/__portable/status`, { headers: { 'x-portable-key': state.secret }, signal: AbortSignal.timeout(1000) });
    const body = await response.json();
    return response.ok && body.pid === state.pid && body.kind === config.kind;
  } catch { return false; }
}
function openBrowser(url) {
  if (process.argv.includes('--no-browser')) return;
  const child = spawn(process.env.ComSpec ?? 'cmd.exe', ['/d', '/c', 'start', '""', url], { windowsHide: true, stdio: 'ignore' });
  child.on('error', () => console.log(`请在浏览器打开：${url}`));
}

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif', '.svg': 'image/svg+xml', '.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg', '.mp4': 'video/mp4', '.webm': 'video/webm', '.woff2': 'font/woff2' };
function inRoot(root, target) { const rel = path.relative(root, target); return rel !== '..' && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel); }

async function daemon() {
  if (existsSync(lockFile)) {
    const lock = json(lockFile);
    if (lock && alive(lock.pid)) return;
    rmSync(lockFile, { force: true });
  }
  let lock;
  try { lock = openSync(lockFile, 'wx'); } catch (error) { if (error.code === 'EEXIST') return; throw error; }
  writeFileSync(lock, JSON.stringify({ pid: process.pid }));
  closeSync(lock);
  let studio;
  let stopped = false;
  const secret = randomBytes(24).toString('hex');
  const root = realpathSync(config.kind === 'editor' ? path.join(here, 'app', 'dist') : here);
  let ready = false;
  const server = createServer((request, response) => {
    const finish = (code, text) => { response.writeHead(code, { 'content-type': 'text/plain; charset=utf-8' }); response.end(text); };
    if (!['GET', 'HEAD', 'POST'].includes(request.method)) { finish(405, 'method not allowed'); return; }
    let pathname;
    try { pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname); } catch { finish(400, 'invalid path'); return; }
    // The editor page can rediscover its backend after a restart without exposing launcher credentials.
    if (pathname === '/__studio/discovery') {
      if (config.kind !== 'editor') { finish(404, 'not found'); return; }
      if (request.method !== 'GET') { finish(405, 'method not allowed'); return; }
      if (!ready || !studio?.url) { finish(503, 'starting'); return; }
      const origin = `http://127.0.0.1:${server.address().port}`;
      const requestOrigin = request.headers.origin;
      const site = request.headers['sec-fetch-site'];
      if ((requestOrigin && requestOrigin !== origin) || (site && site !== 'same-origin' && site !== 'none')) { finish(403, 'forbidden'); return; }
      response.writeHead(200, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' });
      response.end(JSON.stringify({ apiUrl: studio.url }));
      return;
    }
    if (pathname.startsWith('/__portable/')) {
      if (request.headers['x-portable-key'] !== secret) { finish(403, 'forbidden'); return; }
      if (pathname === '/__portable/status' && ready) {
        response.writeHead(200, { 'content-type': 'application/json' });
        response.end(JSON.stringify({ pid: process.pid, kind: config.kind }));
      } else if (pathname === '/__portable/stop' && request.method === 'POST') {
        finish(200, 'stopping'); void shutdown();
      } else finish(503, 'starting');
      return;
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') { finish(405, 'method not allowed'); return; }
    if (pathname === '/favicon.ico') { finish(204, ''); return; }
    const rel = pathname === '/' ? (config.kind === 'editor' ? 'studio-live.html' : 'player.html') : pathname.replace(/^[/\\]+/, '');
    // 展示包只开放播放器和作品内容，运行环境、日志、控制密钥不经过 HTTP 提供。
    if (config.kind === 'player' && !['player.html'].includes(rel) && !/^(assets|content)\//.test(rel)) { finish(403, 'forbidden'); return; }
    const target = path.resolve(root, rel);
    if (!inRoot(root, target)) { finish(403, 'forbidden'); return; }
    try {
      if (!inRoot(root, realpathSync(target)) || !statSync(target).isFile()) { finish(403, 'forbidden'); return; }
      const size = statSync(target).size;
      const headers = { 'content-type': MIME[path.extname(target).toLowerCase()] ?? 'application/octet-stream', 'accept-ranges': 'bytes', 'cache-control': 'no-cache', 'x-content-type-options': 'nosniff' };
      let start = 0; let end = size - 1; let status = 200;
      if (request.headers.range) {
        const match = /^bytes=(\d*)-(\d*)$/.exec(request.headers.range);
        if (!match || (!match[1] && !match[2])) { finish(416, 'invalid range'); return; }
        if (!match[1]) start = Math.max(0, size - Number(match[2]));
        else { start = Number(match[1]); if (match[2]) end = Math.min(end, Number(match[2])); }
        if (start > end || start >= size) { response.writeHead(416, { 'content-range': `bytes */${size}` }); response.end(); return; }
        status = 206; headers['content-range'] = `bytes ${start}-${end}/${size}`;
      }
      headers['content-length'] = size ? end - start + 1 : 0;
      response.writeHead(status, headers);
      if (request.method === 'HEAD' || !size) response.end();
      else createReadStream(target, { start, end }).on('error', () => response.destroy()).pipe(response);
    } catch { finish(404, 'not found'); }
  });
  async function shutdown() {
    if (stopped) return;
    stopped = true; ready = false;
    try { await studio?.close(); } finally {
      server.closeAllConnections();
      await new Promise((resolve) => server.close(resolve));
      if (json(stateFile)?.pid === process.pid) rmSync(stateFile, { force: true });
      if (json(lockFile)?.pid === process.pid) rmSync(lockFile, { force: true });
      process.exit(0);
    }
  }
  async function listen(port) {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(port, '127.0.0.1', () => { server.removeListener('error', reject); resolve(); });
    });
  }
  try {
    try { await listen(json(preferencesFile)?.port ?? (config.kind === 'editor' ? 5190 : 5199)); }
    catch (error) { if (error.code !== 'EADDRINUSE') throw error; await listen(0); }
    const origin = `http://127.0.0.1:${server.address().port}`;
    writeFileSync(preferencesFile, JSON.stringify({ port: server.address().port }));
    let url = `${origin}/player.html`;
    if (config.kind === 'editor') {
      const workspace = path.join(dataDir, 'workspace');
      // 只在工作区尚未创建时放入样例，后续启动不覆盖作者内容。
      if (!existsSync(workspace) && existsSync(path.join(here, 'seed-workspace'))) copyTree(path.join(here, 'seed-workspace'), workspace);
      const { startService } = await import(pathToFileURL(path.join(here, 'app', 'server', 'src', 'main.ts')).href);
      studio = await startService({ workspaceRoot: workspace, host: '127.0.0.1', port: 0, printDiscovery: false, initPairing: false, uiOrigins: [origin] });
      url = `${origin}/studio-live.html?api=${encodeURIComponent(studio.url)}`;
    }
    ready = true;
    writeFileSync(stateFile, JSON.stringify({ pid: process.pid, kind: config.kind, origin, url, secret }, null, 2));
    process.on('SIGINT', () => { void shutdown(); });
    process.on('SIGTERM', () => { void shutdown(); });
  } catch (error) {
    await studio?.close(); server.close();
    if (json(lockFile)?.pid === process.pid) rmSync(lockFile, { force: true });
    throw error;
  }
}

async function main() {
  if (process.argv.includes('--daemon')) { await daemon(); return; }
  const state = json(stateFile);
  if (process.argv.includes('stop')) {
    if (!await probe(state)) { console.log('服务未运行。'); return; }
    await fetch(`${state.origin}/__portable/stop`, { method: 'POST', headers: { 'x-portable-key': state.secret }, signal: AbortSignal.timeout(10000) });
    for (let n = 0; n < 100 && await probe(state); n++) await delay(100);
    if (await probe(state)) throw new Error('服务停止超时，请查看 data/launcher.log');
    console.log('服务已停止。'); return;
  }
  if (await probe(state)) { console.log(state.url); openBrowser(state.url); return; }
  const log = openSync(path.join(dataDir, 'launcher.log'), 'a');
  const child = spawn(process.execPath, [fileURLToPath(import.meta.url), '--daemon'], { cwd: here, detached: true, windowsHide: true, stdio: ['ignore', log, log] });
  let spawnError;
  child.on('error', (error) => { spawnError = error; }); child.unref(); closeSync(log);
  for (let n = 0; n < 200; n++) {
    if (spawnError) throw spawnError;
    await delay(100);
    const ready = json(stateFile);
    if (await probe(ready)) { console.log(ready.url); openBrowser(ready.url); return; }
  }
  throw new Error('启动失败，请查看 data/launcher.log；可先双击停止脚本后重试。');
}
main().catch((error) => { console.error(error.stack ?? error.message); process.exitCode = 1; });
