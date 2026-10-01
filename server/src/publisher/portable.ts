import { copyFileSync, existsSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { copyDirectory } from '../store/fsx.ts';

export function portableCommand(script: string, args = ''): string {
  return ['@echo off', 'chcp 65001 >nul', 'setlocal', 'cd /d "%~dp0"', `"%~dp0runtime\\node.exe" "%~dp0${script}" ${args} %*`, 'if errorlevel 1 (echo 启动失败，请查看 data\\launcher.log。& pause & exit /b 1)', 'endlocal', ''].join('\r\n');
}

// 制作包的后台导出时，直接附带同一个 Windows 运行环境与启动器。
// 开发目录没有 runtime/，继续保留原先仅内容的导出方式。
export function installPlayerPortable(directory: string, runtimeRoot: string, launcher: string): boolean {
  if (!existsSync(path.join(runtimeRoot, 'node.exe'))) return false;
  if (!existsSync(path.join(runtimeRoot, 'node-LICENSE.txt')) || !existsSync(launcher)) throw new Error('便携运行环境不完整，缺少许可证或启动器');
  copyDirectory(runtimeRoot, path.join(directory, 'runtime'));
  copyFileSync(launcher, path.join(directory, 'launch.mjs'));
  writeFileSync(path.join(directory, 'portable.json'), `${JSON.stringify({ kind: 'player', platform: 'win-x64' }, null, 2)}\n`);
  writeFileSync(path.join(directory, '启动展示器.cmd'), portableCommand('launch.mjs'));
  writeFileSync(path.join(directory, '停止展示器.cmd'), portableCommand('launch.mjs', 'stop'));
  // 保留简洁英文入口，便于脚本及非中文系统。
  writeFileSync(path.join(directory, 'player.cmd'), portableCommand('launch.mjs'));
  writeFileSync(path.join(directory, 'README.txt'), [
    '拾光 Studio 展示包 — Windows 10/11 64 位',
    '请先完整解压到可写文件夹，再双击“启动展示器.cmd”，作品会在默认浏览器打开。',
    '已内置 Node 运行环境，无需安装 Node、Python、npm 或 Docker。',
    '关闭浏览器后，可双击“停止展示器.cmd”结束本地服务。',
    '本包若未附作品，请在编辑器中点击“导出作品”，将导出目录中的整个 content 文件夹复制到本目录。',
    'content/content.json 是作品入口；content/assets/ 和 content/levels/ 要一并复制。',
    '后台日志与端口偏好保存在 data/，服务只监听本机。',
    '存档保存在当前浏览器；更换浏览器、文件夹或端口后原存档可能不可见。',
    '运行环境与第三方许可证见 runtime/ 和 licenses/。',
    '',
  ].join('\r\n'));
  return true;
}
