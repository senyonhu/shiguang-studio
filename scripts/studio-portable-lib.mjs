import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';

export const sha256 = (data) => createHash('sha256').update(data).digest('hex');
export function walk(dir, prefix = '') {
  return readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name)).flatMap((entry) => {
    if (entry.isSymbolicLink()) throw new Error(`打包拒绝符号链接：${entry.name}`);
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    return entry.isDirectory() ? walk(path.join(dir, entry.name), rel) : [rel];
  });
}
export function copyTree(from, to, filter = () => true) {
  for (const rel of walk(from)) {
    if (!filter(rel)) continue;
    const target = path.join(to, rel); mkdirSync(path.dirname(target), { recursive: true }); copyFileSync(path.join(from, rel), target);
  }
}
function within(parent, target) { const rel = path.relative(parent, target); return rel && rel !== '..' && !rel.startsWith(`..${path.sep}`) && !path.isAbsolute(rel); }
export function prepareOutput(root, output) {
  // 只允许仓库或临时目录内的交付目录；禁止删除源码/运行数据等既有文件夹。
  const allowed = [root, os.tmpdir()].some((parent) => within(path.resolve(parent), output));
  if (!allowed || output === root || ['src', 'server', 'scripts', 'tests', 'review', 'artifacts', '.runtime', 'packaging', 'node_modules', 'tools', 'release'].includes(path.relative(root, output).split(path.sep)[0]) && path.dirname(output) === root) throw new Error(`不安全的打包输出目录：${output}`);
  let ancestor = output;
  while (!existsSync(ancestor)) ancestor = path.dirname(ancestor);
  if (![realpathSync(root), realpathSync(os.tmpdir())].some((parent) => ancestor === parent || within(parent, realpathSync(ancestor)))) throw new Error('输出目录解析到许可范围之外');
  if (existsSync(output)) {
    const manifest = path.join(output, 'manifest.json');
    if (!existsSync(manifest) || JSON.parse(readFileSync(manifest, 'utf8')).kind !== 'shiguang-studio-portable') throw new Error('已有目录不是制作交付包，拒绝覆盖');
    if (existsSync(path.join(output, 'data'))) throw new Error('交付目录含用户数据，请选择新的 --out 目录');
    rmSync(output, { recursive: true, force: true });
  }
  mkdirSync(output, { recursive: true });
}
export function copyRuntime(root, output, executable = process.execPath) {
  if (process.platform !== 'win32' || process.arch !== 'x64') throw new Error('此打包入口针对 Windows x64');
  const version = execFileSync(executable, ['--version'], { encoding: 'utf8' }).trim();
  if (version !== 'v24.14.1') throw new Error(`运行时版本 ${version} 与已收录许可证不一致，请先更新 packaging/studio/`);
  const digest = sha256(readFileSync(executable));
  const expected = readFileSync(path.join(root, 'packaging/studio/node-SHASUMS256.txt'), 'utf8').split(/\r?\n/).find((line) => line.endsWith('  win-x64/node.exe'))?.split(/\s+/)[0];
  if (!expected || digest !== expected) throw new Error('Node 运行时与官方 SHA256 不一致');
  mkdirSync(output, { recursive: true }); copyFileSync(executable, path.join(output, 'node.exe'));
  copyFileSync(path.join(root, 'packaging/studio/node-LICENSE.txt'), path.join(output, 'node-LICENSE.txt'));
  writeFileSync(path.join(output, 'version.json'), `${JSON.stringify({ version, platform: 'win-x64', sha256: digest }, null, 2)}\n`);
  return version;
}
export function refreshManifest(directory) {
  const file = path.join(directory, 'manifest.json');
  const manifest = JSON.parse(readFileSync(file, 'utf8'));
  const player = manifest.kind !== 'shiguang-studio-portable';
  manifest.files = walk(directory).filter((rel) => rel !== 'manifest.json').map((rel) => ({ rel, [player ? 'size' : 'bytes']: statSync(path.join(directory, rel)).size, sha256: sha256(readFileSync(path.join(directory, rel))) }));
  manifest.fileCount = manifest.files.length;
  manifest.totalBytes = manifest.files.reduce((n, file) => n + (file.size ?? file.bytes), 0);
  if (player) manifest.contentDigest = sha256(manifest.files.map((file) => `${file.sha256}  ${file.rel}`).sort().join('\n'));
  writeFileSync(file, `${JSON.stringify(manifest, null, 2)}\n`);
  return manifest;
}
export function zipDirectory(directory) {
  const destination = `${directory}.zip`;
  if (existsSync(destination)) throw new Error(`压缩包已存在，请先选择新的输出名：${destination}`);
  execFileSync(path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe'), ['-NoProfile', '-Command', 'Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::CreateFromDirectory($env:STUDIO_ZIP_SOURCE, $env:STUDIO_ZIP_DEST, [IO.Compression.CompressionLevel]::Optimal, $false)'], { windowsHide: true, stdio: 'pipe', env: { ...process.env, STUDIO_ZIP_SOURCE: directory, STUDIO_ZIP_DEST: destination } });
  return destination;
}
