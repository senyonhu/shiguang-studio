// This source is emitted as a self-contained server in every exported player.
export const PLAYER_SERVE_SCRIPT = String.raw`import http from 'node:http';
import { createReadStream, lstatSync, realpathSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = realpathSync(path.dirname(fileURLToPath(import.meta.url)));
const index = process.argv.indexOf('--port');
const port = index < 0 ? 5199 : Number(process.argv[index + 1]);
if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('Invalid local server port');
const MIME = { '.html':'text/html; charset=utf-8', '.htm':'text/html; charset=utf-8', '.js':'text/javascript; charset=utf-8', '.mjs':'text/javascript; charset=utf-8', '.css':'text/css; charset=utf-8', '.json':'application/json; charset=utf-8', '.png':'image/png', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.webp':'image/webp', '.gif':'image/gif', '.svg':'image/svg+xml', '.wav':'audio/wav', '.mp3':'audio/mpeg', '.ogg':'audio/ogg', '.mp4':'video/mp4', '.webm':'video/webm', '.wasm':'application/wasm', '.woff':'font/woff', '.woff2':'font/woff2' };
const inside = (target) => { const relative = path.relative(root, target); return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative); };
http.createServer((request, response) => {
  const finish = (status, message) => { response.writeHead(status, { 'content-type':'text/plain; charset=utf-8' }); response.end(request.method === 'HEAD' ? undefined : message); };
  if (!['GET', 'HEAD'].includes(request.method)) return finish(405, 'method not allowed');
  let relative;
  try { relative = decodeURIComponent(new URL(request.url ?? '/', 'http://127.0.0.1').pathname).replace(/^\/+/, ''); } catch { return finish(400, 'invalid path'); }
  if (!relative) relative = 'player.html';
  if (relative === 'favicon.ico') return finish(204, '');
  if (relative !== 'player.html' && !/^(assets|content)\//.test(relative)) return finish(403, 'forbidden');
  if (/[\\:\0]/.test(relative) || relative.split('/').some((part) => !part || part.startsWith('.'))) return finish(403, 'forbidden');
  try {
    let target = root;
    for (const part of relative.split('/')) { target = path.join(target, part); if (lstatSync(target).isSymbolicLink()) return finish(403, 'forbidden'); }
    if (!inside(realpathSync(target)) || !statSync(target).isFile()) return finish(403, 'forbidden');
    const size = statSync(target).size;
    let start = 0, end = size - 1, status = 200;
    const headers = { 'content-type': MIME[path.extname(target).toLowerCase()] ?? 'application/octet-stream', 'cache-control':'no-store', 'x-content-type-options':'nosniff', 'referrer-policy':'no-referrer', 'accept-ranges':'bytes' };
    if (request.headers.range) {
      const range = /^bytes=(\d*)-(\d*)$/.exec(request.headers.range);
      if (!range || (!range[1] && !range[2])) { response.writeHead(416, { 'content-range':'bytes */' + size }); response.end(); return; }
      if (!range[1]) start = Math.max(0, size - Number(range[2]));
      else { start = Number(range[1]); if (range[2]) end = Math.min(end, Number(range[2])); }
      if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start > end || start >= size) { response.writeHead(416, { 'content-range':'bytes */' + size }); response.end(); return; }
      status = 206; headers['content-range'] = 'bytes ' + start + '-' + end + '/' + size;
    }
    headers['content-length'] = size ? end - start + 1 : 0;
    response.writeHead(status, headers);
    if (request.method === 'HEAD' || !size) response.end();
    else createReadStream(target, { start, end }).on('error', () => response.destroy()).pipe(response);
  } catch { finish(404, 'not found'); }
}).listen(port, '127.0.0.1', function () { process.stdout.write('Player ready: http://127.0.0.1:' + this.address().port + '/player.html\n'); });
`;
