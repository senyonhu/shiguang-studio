// 插件打包：把 MCP 工具适配器打包成插件目录内的构建产物，并写出包内文件清单。
// 用法：node scripts/build-plugin.mjs
import { build } from 'esbuild';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const PLUGIN_DIR = path.join(ROOT, 'plugin', 'studio-author');
const SERVER_DIR = path.join(PLUGIN_DIR, 'server');

rmSync(SERVER_DIR, { recursive: true, force: true });
mkdirSync(SERVER_DIR, { recursive: true });

const result = await build({
  entryPoints: [path.join(ROOT, 'server', 'src', 'mcp-stdio.ts')],
  outfile: path.join(SERVER_DIR, 'mcp-stdio.mjs'),
  bundle: true,
  platform: 'node',
  format: 'esm',
  target: 'node22',
  sourcemap: false,
  logLevel: 'warning',
  banner: { js: '// 由 npm run build:plugin 生成：请勿直接编辑；源码在 server/src/**。' },
  metafile: true,
});

function listFiles(dir, prefix = '') {
  const out = [];
  for (const name of readdirSync(dir).sort()) {
    if (name === 'manifest.json') continue;
    const abs = path.join(dir, name);
    if (statSync(abs).isDirectory()) out.push(...listFiles(abs, `${prefix}${name}/`));
    else out.push({ rel: `${prefix}${name}`, size: statSync(abs).size, sha256: createHash('sha256').update(readFileSync(abs)).digest('hex') });
  }
  return out;
}

const files = listFiles(PLUGIN_DIR);
const manifest = {
  kind: 'studio-author-plugin-package',
  builtAt: new Date().toISOString(),
  entry: 'server/mcp-stdio.mjs',
  fileCount: files.length,
  totalBytes: files.reduce((sum, file) => sum + file.size, 0),
  files,
  bundler: { tool: 'esbuild', version: result.metafile ? undefined : undefined, inputs: Object.keys(result.metafile?.inputs ?? {}).length },
  note: '包内不包含任何凭据；会话在运行时通过本机配对密钥建立。字段与安装方式仍需按目标宿主规范核对。',
};
writeFileSync(path.join(PLUGIN_DIR, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, 'utf8');

const serverSize = statSync(path.join(SERVER_DIR, 'mcp-stdio.mjs')).size;
console.log(`打包完成：server/mcp-stdio.mjs ${serverSize} 字节；包内文件 ${files.length} 个，共 ${manifest.totalBytes} 字节`);
console.log(`清单：${path.relative(ROOT, path.join(PLUGIN_DIR, 'manifest.json'))}`);
