// 便携包内的静态托管：把编辑器/展示器构建产物用最小 HTTP 服务提供（只监听本机，不访问外部网络）。
// 用法：node scripts/studio-pack-serve.mjs --root <目录> [--port 5173]
import { createReadStream, lstatSync, realpathSync, statSync } from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { argValue } from './studio-lib.mjs';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const rootDir = realpathSync(path.resolve(ROOT, argValue(process.argv, '--root', 'dist')));
const port = Number(argValue(process.argv, '--port', '5173'));
if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('本机静态服务端口必须是 0–65535 的整数');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.htm': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
  '.gif': 'image/gif',
  '.wav': 'audio/wav',
  '.mp3': 'audio/mpeg',
  '.ogg': 'audio/ogg',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.wasm': 'application/wasm',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
};

function insideRoot(target) {
  const relative = path.relative(rootDir, target);
  return relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}

const server = http.createServer((req, res) => {
  const finish = (status, text, headers = {}) => {
    res.writeHead(status, { 'content-type': 'text/plain; charset=utf-8', ...headers });
    res.end(req.method === 'HEAD' || status === 204 ? undefined : text);
  };
  if (!['GET', 'HEAD'].includes(req.method)) return finish(405, 'method not allowed', { allow: 'GET, HEAD' });
  let relative;
  try {
    relative = decodeURIComponent(new URL(req.url ?? '/', 'http://127.0.0.1').pathname).replace(/^\/+/, '') || 'index.html';
  } catch {
    return finish(400, 'invalid path');
  }
  if (/[\\:\0]/.test(relative) || relative.split('/').some((part) => !part || part.startsWith('.'))) return finish(403, 'forbidden');
  try {
    let target = rootDir;
    for (const part of relative.split('/')) {
      target = path.join(target, part);
      if (lstatSync(target).isSymbolicLink()) return finish(403, 'forbidden');
    }
    if (!insideRoot(realpathSync(target))) return finish(403, 'forbidden');
    const file = statSync(target);
    if (!file.isFile()) return finish(404, 'not found');
    const type = MIME[path.extname(target).toLowerCase()] ?? 'application/octet-stream';
    res.writeHead(200, { 'content-type': type, 'content-length': file.size, 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', 'referrer-policy': 'no-referrer' });
    if (req.method === 'HEAD' || !file.size) res.end();
    else createReadStream(target).on('error', () => res.destroy()).pipe(res);
  } catch {
    if (relative === 'favicon.ico') return finish(204, '');
    finish(404, 'not found');
  }
});

server.listen(port, '127.0.0.1', () => {
  console.log(`静态托管已启动：http://127.0.0.1:${server.address().port}/studio-live.html（根目录 ${rootDir}）`);
});
