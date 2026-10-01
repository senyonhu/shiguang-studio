// 发布包验收检查：完整性（清单散列）、素材齐全、敏感信息与本地路径排除，并可实际启动冒烟。
// 用法：node scripts/studio-release-check.mjs --package <发布包目录> [--json <报告>] [--run]
// 说明：本脚本只读取发布包，不修改工程；--run 会启动包内 serve.mjs 并抓取播放器与内容包。
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { argValue } from './studio-lib.mjs';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const packageDir = path.resolve(ROOT, argValue(process.argv, '--package', ''));
const checks = [];
const fail = (id, message, details) => checks.push({ id, status: 'failed', message, details });
const pass = (id, message, details) => checks.push({ id, status: 'passed', message, details });
const warn = (id, message, details) => checks.push({ id, status: 'warning', message, details });

const sha256 = (buffer) => createHash('sha256').update(buffer).digest('hex');

function walk(dir, prefix = '') {
  const out = [];
  for (const name of readdirSync(dir)) {
    const abs = path.join(dir, name);
    const rel = prefix ? `${prefix}/${name}` : name;
    if (statSync(abs).isDirectory()) out.push(...walk(abs, rel));
    else out.push(rel);
  }
  return out;
}

if (!packageDir || !existsSync(path.join(packageDir, 'manifest.json'))) {
  console.error(`用法：node scripts/studio-release-check.mjs --package <发布包目录>（未找到 ${path.join(packageDir || '(空)', 'manifest.json')}）`);
  process.exit(2);
}

// 1. 清单与文件一一对应，散列一致
const manifest = JSON.parse(readFileSync(path.join(packageDir, 'manifest.json'), 'utf8'));
const onDisk = walk(packageDir).filter((rel) => rel !== 'manifest.json');
const manifestFiles = new Map(manifest.files.map((file) => [file.rel, file]));
const missing = [...manifestFiles.keys()].filter((rel) => !existsSync(path.join(packageDir, rel)));
const extra = onDisk.filter((rel) => !manifestFiles.has(rel));
const mismatched = [];
for (const [rel, entry] of manifestFiles) {
  const abs = path.join(packageDir, rel);
  if (!existsSync(abs)) continue;
  if (sha256(readFileSync(abs)) !== entry.sha256) mismatched.push(rel);
}
if (missing.length) fail('RELEASE-FILES-PRESENT', `清单中存在但包内缺失的文件：${missing.join(', ')}`);
else if (extra.length) warn('RELEASE-FILES-PRESENT', `包内存在清单未列出的文件：${extra.join(', ')}`);
else pass('RELEASE-FILES-PRESENT', `清单与包内 ${onDisk.length} 个文件一一对应`);
if (mismatched.length) fail('RELEASE-HASHES', `散列不一致的文件：${mismatched.join(', ')}`);
else pass('RELEASE-HASHES', `全部 ${manifestFiles.size} 个文件散列与清单一致`);

// 2. 内容包与素材齐全
const contentFile = path.join(packageDir, 'content', 'content.json');
let bundle = null;
if (!existsSync(contentFile)) fail('RELEASE-CONTENT', '缺少 content/content.json');
else {
  try {
    bundle = JSON.parse(readFileSync(contentFile, 'utf8'));
    const scenes = Array.isArray(bundle.scenes) ? bundle.scenes : [];
    const events = scenes.flatMap((scene) => scene.events ?? []);
    const assetIds = new Set((bundle.assets ?? []).map((asset) => asset.assetId));
    const referenced = new Set();
    for (const event of events) {
      const params = event.params ?? {};
      if (typeof params.assetId === 'string' && params.assetId) referenced.add(params.assetId);
      if (typeof params.characterId === 'string' && params.characterId) referenced.add(params.characterId);
      for (const option of Array.isArray(params.options) ? params.options : []) {
        if (option && typeof option.assetId === 'string' && option.assetId) referenced.add(option.assetId);
      }
    }
    const missingAssets = [...referenced].filter((assetId) => !assetIds.has(assetId));
    const filesMissing = (bundle.assets ?? []).filter((asset) => !existsSync(path.join(packageDir, 'content', asset.path)));
    if (missingAssets.length) fail('RELEASE-ASSET-REFS', `剧情引用了内容包中没有的素材：${missingAssets.join(', ')}`);
    else if (filesMissing.length) fail('RELEASE-ASSET-FILES', `素材清单中的文件缺失：${filesMissing.map((asset) => asset.path).join(', ')}`);
    else pass('RELEASE-ASSET-COMPLETE', `${(bundle.assets ?? []).length} 个登记素材文件齐全，剧情引用全部可解析`, { referenceCount: referenced.size, eventCount: events.length, sceneCount: scenes.length });
    const hashMismatch = (bundle.assets ?? []).filter((asset) => {
      const abs = path.join(packageDir, 'content', asset.path);
      return existsSync(abs) && sha256(readFileSync(abs)) !== asset.sha256;
    });
    if (hashMismatch.length) fail('RELEASE-ASSET-HASH', `素材内容与清单散列不一致：${hashMismatch.map((asset) => asset.assetId).join(', ')}`);
    else pass('RELEASE-ASSET-HASH', '素材内容散列与内容包一致');
  } catch (error) {
    fail('RELEASE-CONTENT', `内容包不可解析：${error.message}`);
  }
}

// 3. 敏感信息与本地路径排除
const secretPatterns = [
  { id: 'pairing-key', pattern: /"pairingKey"\s*:/i },
  { id: 'session-token', pattern: /(STUDIO_SESSION_TOKEN|"tokenHash"|Bearer\s+[0-9a-f]{32,})/ },
  { id: 'service-dir', pattern: /\.studio-service/ },
  { id: 'runtime-state', pattern: /(projectId"\s*:\s*"[a-z]{3}_[a-z0-9]+"[\s\S]{0,200}"idempotency")/ },
  { id: 'absolute-windows-path', pattern: /[A-Za-z]:\\\\?Users\\\\?|\/Users\// },
];
const scanned = [];
for (const rel of onDisk) {
  const abs = path.join(packageDir, rel);
  if (statSync(abs).size > 4 * 1024 * 1024) continue;
  const ext = path.extname(rel).toLowerCase();
  if (!['.json', '.html', '.js', '.mjs', '.css', '.txt', '.md'].includes(ext)) continue;
  scanned.push(rel);
}
const hits = [];
for (const rel of scanned) {
  const text = readFileSync(path.join(packageDir, rel), 'utf8');
  for (const { id, pattern } of secretPatterns) {
    if (pattern.test(text)) hits.push(`${rel}:${id}`);
  }
}
for (const rel of onDisk) {
  if (/pairing\.key|sessions\.json|credentials|\.studio[\\/]|token/i.test(rel)) hits.push(`${rel}:filename`);
}
if (hits.length) fail('RELEASE-NO-SECRETS', `发布包中出现疑似凭据/运行数据/本地路径：${hits.join(', ')}`);
else pass('RELEASE-NO-SECRETS', `扫描 ${scanned.length} 个文本文件：无凭据、会话文件、运行数据或本地绝对路径`);

// 4. 本地路径依赖（内容包中不得引用工程绝对路径）
if (bundle) {
  const text = JSON.stringify(bundle);
  const badPaths = [/[A-Za-z]:\\\\/, /file:\/\//, /127\.0\.0\.1:\d+/].filter((pattern) => pattern.test(text));
  if (badPaths.length) fail('RELEASE-NO-LOCAL-DEPS', `内容包引用了本地绝对路径或本地服务地址（${badPaths.length} 类）`);
  else pass('RELEASE-NO-LOCAL-DEPS', '内容包只使用包内相对路径，不依赖本地绝对路径或本机端口');
}

// 5. 可选：实际启动发布包并抓取播放器与内容包
async function runSmoke() {
  const serve = path.join(packageDir, 'serve.mjs');
  if (!existsSync(serve)) return fail('RELEASE-SMOKE', '缺少 serve.mjs，无法启动冒烟');
  const child = spawn(process.execPath, [serve, '--port', '0'], { stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '';
  const url = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`serve.mjs 启动超时：${output}`)), 15000);
    child.stdout?.on('data', (chunk) => {
      output += chunk.toString();
      const match = /http:\/\/127\.0\.0\.1:(\d+)\/player\.html/.exec(output);
      if (match) { clearTimeout(timer); resolve(match[0]); }
    });
    child.on('close', () => { clearTimeout(timer); reject(new Error(`serve.mjs 提前退出：${output}`)); });
  }).catch((error) => ({ error: error.message }));
  if (typeof url === 'object') {
    child.kill('SIGKILL');
    return fail('RELEASE-SMOKE', url.error);
  }
  try {
    const player = await fetch(url);
    const content = await fetch(url.replace('player.html', 'content/content.json'));
    const bundleText = await content.text();
    if (!player.ok) fail('RELEASE-SMOKE', `player.html 返回 HTTP ${player.status}`);
    else if (!content.ok) fail('RELEASE-SMOKE', `content.json 返回 HTTP ${content.status}`);
    else pass('RELEASE-SMOKE', `发布包可启动：player.html ${player.status}、内容包 ${bundleText.length} 字节`, { url });
  } finally {
    child.kill('SIGKILL');
  }
}
if (process.argv.includes('--run')) await runSmoke();

// 材料一致性（KI-032/KI-033 的教训）：review/studio 里写的指纹与数字必须等于证据包实测结果
try {
  const materialsOut = execFileSync(process.execPath, [path.join(ROOT, 'scripts', 'studio-materials-check.mjs')], { encoding: 'utf8' });
  const summary = materialsOut.trim().split('\n').slice(-1)[0] ?? '';
  pass('MATERIALS-CONSISTENCY', `材料与证据包一致（${summary}）`);
} catch (error) {
  const detail = `${error.stdout ?? ''}${error.stderr ?? ''}`.trim().split('\n').filter((line) => line.includes('failed')).join('; ');
  fail('MATERIALS-CONSISTENCY', `材料与证据包不一致：${detail || error.message}`);
}

const report = {
  kind: 'studio-release-check',
  checkedAt: new Date().toISOString(),
  packageDir: path.relative(ROOT, packageDir).replace(/\\/g, '/'),
  revision: manifest.revision ?? null,
  exportId: manifest.exportId ?? null,
  fileCount: onDisk.length,
  totalBytes: onDisk.reduce((sum, rel) => sum + statSync(path.join(packageDir, rel)).size, 0),
  checks,
  summary: {
    passed: checks.filter((check) => check.status === 'passed').length,
    failed: checks.filter((check) => check.status === 'failed').length,
    warnings: checks.filter((check) => check.status === 'warning').length,
  },
};
const out = argValue(process.argv, '--json');
if (out) {
  const abs = path.resolve(ROOT, out);
  writeFileSync(abs, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  console.log(`报告已写入 ${path.relative(ROOT, abs)}`);
}
for (const check of checks) console.log(`[${check.status}] ${check.id}: ${check.message}${check.details ? ` ${JSON.stringify(check.details)}` : ''}`);
console.log(`passed=${report.summary.passed} failed=${report.summary.failed} warnings=${report.summary.warnings} files=${report.fileCount} bytes=${report.totalBytes}`);
if (report.summary.failed > 0) process.exitCode = 1;
