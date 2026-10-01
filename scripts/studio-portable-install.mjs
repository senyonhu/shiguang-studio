// 可选目录安装：只更新程序文件，保留作者 data/；只允许删除有制作包标识的目标。
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const argv = process.argv.slice(2);
const value = (flag, fallback) => { const i = argv.indexOf(flag); return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback; };
const prefix = path.resolve(value('--prefix', path.join(process.env.LOCALAPPDATA ?? os.homedir(), 'ShiguangStudio')));
const shortcutDir = argv.includes('--no-shortcut') ? '' : path.resolve(value('--shortcut-dir', path.join(prefix, 'shortcuts')));
function copyEntry(from, to) {
  if (statSync(from).isDirectory()) {
    mkdirSync(to, { recursive: true });
    for (const entry of readdirSync(from)) copyEntry(path.join(from, entry), path.join(to, entry));
  } else copyFileSync(from, to);
}
if (prefix === here || here.startsWith(prefix + path.sep) || prefix === path.parse(prefix).root) throw new Error('安装目标不能是当前包、自身父目录或磁盘根目录');
const marker = path.join(prefix, 'manifest.json');
const managed = existsSync(marker) && JSON.parse(readFileSync(marker, 'utf8')).kind === 'shiguang-studio-portable';
if (path.basename(fileURLToPath(import.meta.url)) === 'uninstall.mjs') {
  if (!managed) throw new Error('目标不是已安装的拾光 Studio，拒绝删除');
  if (existsSync(path.join(prefix, 'data/startup.lock'))) throw new Error('请先停止编辑器再卸载');
  rmSync(prefix, { recursive: true, force: true });
  if (shortcutDir && !shortcutDir.startsWith(prefix + path.sep)) rmSync(path.join(shortcutDir, '拾光 Studio.lnk'), { force: true });
  console.log(`已卸载 ${prefix}`);
} else {
  if (existsSync(prefix) && readdirSync(prefix).length && !managed) throw new Error('安装目标不是空目录或既有制作包，拒绝覆盖');
  if (existsSync(path.join(prefix, 'data/startup.lock'))) throw new Error('请先停止编辑器再更新');
  mkdirSync(prefix, { recursive: true });
  for (const entry of readdirSync(here)) if (entry !== 'data') copyEntry(path.join(here, entry), path.join(prefix, entry));
  if (shortcutDir) {
    mkdirSync(shortcutDir, { recursive: true });
    const quote = (text) => text.replace(/'/g, "''");
    const script = `$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('${quote(path.join(shortcutDir, '拾光 Studio.lnk'))}'); $s.TargetPath = '${quote(path.join(prefix, 'studio.cmd'))}'; $s.WorkingDirectory = '${quote(prefix)}'; $s.Save()`;
    execFileSync(path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe'), ['-NoProfile', '-Command', script], { windowsHide: true });
  }
  console.log(`已安装 ${prefix}`);
}
