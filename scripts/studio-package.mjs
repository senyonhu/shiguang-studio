// Windows x64 制作包，内置 Node，不依赖 npm 或机器上的全局运行环境。
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { argValue } from './studio-lib.mjs';
import { copyTree, copyRuntime, prepareOutput, refreshManifest, zipDirectory } from './studio-portable-lib.mjs';
import { portableCommand } from '../server/src/publisher/portable.ts';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const outDir = path.resolve(ROOT, argValue(process.argv, '--out', 'dist-studio'));
const required = ['dist-editor/studio-live.html', 'dist-player/player.html', 'plugin/studio-author/manifest.json', 'plugin/studio-author/server/mcp-stdio.mjs'];
const missing = required.filter((rel) => !existsSync(path.join(ROOT, rel)));
if (missing.length) throw new Error(`缺少构建产物：${missing.join(', ')}；请运行 npm run build:editor、build:player、build:plugin`);
prepareOutput(ROOT, outDir);
const app = path.join(outDir, 'app');
copyTree(path.join(ROOT, 'server'), path.join(app, 'server'));
copyTree(path.join(ROOT, 'src/runtime'), path.join(app, 'src/runtime'));
copyTree(path.join(ROOT, 'dist-editor'), path.join(app, 'dist'), (rel) => !rel.endsWith('.map'));
copyTree(path.join(ROOT, 'dist-player'), path.join(app, 'dist-player'), (rel) => !rel.endsWith('.map'));
copyTree(path.join(ROOT, 'plugin/studio-author'), path.join(app, 'plugin/studio-author'), (rel) => !rel.split('/').includes('node_modules'));
mkdirSync(path.join(app, 'scripts'), { recursive: true });
for (const name of ['studio-lib.mjs', 'studio-release-check.mjs', 'studio-migrate.mjs', 'studio-v1-map.mjs', 'studio-pack-serve.mjs', 'studio-portable-launch.mjs']) cpSync(path.join(ROOT, 'scripts', name), path.join(app, 'scripts', name));
cpSync(path.join(ROOT, 'scripts/studio-portable-launch.mjs'), path.join(outDir, 'launch.mjs'));
const nodeVersion = copyRuntime(ROOT, path.join(outDir, 'runtime'), argValue(process.argv, '--node', process.execPath));
mkdirSync(path.join(outDir, 'licenses'), { recursive: true });
cpSync(path.join(ROOT, 'node_modules/vue/LICENSE'), path.join(outDir, 'licenses/Vue-LICENSE.txt'));
// 作者在离线包中也能阅读完整手册；相关截图和技术说明保持相对链接。
mkdirSync(path.join(outDir, 'docs'), { recursive: true });
for (const name of ['USER_GUIDE.zh-CN.md', 'studio-backend.md', 'AUDIT_2026-10-01.md']) {
  const source = path.join(ROOT, 'docs', name);
  if (name === 'USER_GUIDE.zh-CN.md' && !existsSync(source)) throw new Error('缺少中文使用手册 docs/USER_GUIDE.zh-CN.md');
  if (existsSync(source)) cpSync(source, path.join(outDir, 'docs', name));
}
if (existsSync(path.join(ROOT, 'docs/images'))) copyTree(path.join(ROOT, 'docs/images'), path.join(outDir, 'docs/images'));
// 后台导出时，同样把展示器依赖的许可带入作品包。
copyTree(path.join(outDir, 'licenses'), path.join(app, 'dist-player/licenses'));
const pkg = JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8'));
writeFileSync(path.join(app, 'package.json'), `${JSON.stringify({ name: 'shiguang-studio-portable', version: pkg.version, private: true, type: 'module', engines: { node: '>=22.18' } }, null, 2)}\n`);
writeFileSync(path.join(outDir, 'portable.json'), `${JSON.stringify({ kind: 'editor', platform: 'win-x64' }, null, 2)}\n`);
for (const name of ['studio.cmd', '启动编辑器.cmd']) writeFileSync(path.join(outDir, name), portableCommand('launch.mjs'));
writeFileSync(path.join(outDir, '停止编辑器.cmd'), portableCommand('launch.mjs', 'stop'));
// MCP 接入使用同一运行环境和同一 data/workspace；宿主连接参数见包内说明。
writeFileSync(path.join(outDir, 'mcp.cmd'), portableCommand('app\\plugin\\studio-author\\server\\mcp-stdio.mjs', '--workspace "%~dp0data\\workspace"'));
for (const name of ['install.mjs', 'uninstall.mjs']) cpSync(path.join(ROOT, 'scripts/studio-portable-install.mjs'), path.join(outDir, name));
writeFileSync(path.join(outDir, 'install.cmd'), portableCommand('install.mjs'));
writeFileSync(path.join(outDir, 'README.md'), [
  '# 拾光 Studio 制作编辑器便携版', '',
  '运行环境：Windows 10/11 64 位，使用系统默认浏览器（推荐本机 Edge）。',
  '已内置 Node；无需安装 Node、Python、npm 或 Docker。', '',
  '详细作者/玩家操作与 HTML 关卡契约见 [中文使用手册](docs/USER_GUIDE.zh-CN.md)。', '',
  '完整解压到可写文件夹，再双击 **启动编辑器.cmd**。启动器自动打开浏览器并连接后台，端口占用时自动更换。',
  '关闭浏览器后，双击 **停止编辑器.cmd** 结束后台。重复启动会打开现有实例。', '',
  '- `app/` 是程序、独立展示器模板、制作插件与迁移工具。',
  '- `runtime/` 是 Node 运行环境与许可证，`licenses/` 是前端依赖许可。',
  '- `data/workspace/` 是作者工作区，首次启动创建；备份工程请复制整个工作区。',
  '- `data/launcher.log` 是启动日志。',
  '- `seed-workspace/` 如存在，是首启示例；再次启动不会覆盖已有工作区。', '',
  '无需 Demo 或预先导入素材：在欢迎页或顶部点击“新建作品”，创建第一章与开场；在章节区添加场景，在“配置新事件”中写下对白。',
  '在“素材与作品设置”中选择文件导入背景、立绘、音频或视频；HTML 关卡也可选择文件安装。素材按名称选择，分支通过目标场景/事件选择器配置。',
  '提交修改后可“试玩当前场景”或“从头试玩”；完成后点击“导出作品”。页面显示相对于工作区的导出目录，可“验证导出作品”检查本次发布包。',
  '包内后台导出的作品目录位于 data/workspace/<工程ID>/exports/，内含“启动展示器.cmd”和运行环境；把该目录压缩即可分发。',
  '向单独的空白展示器交付：将导出目录中的整个 content 文件夹复制到展示器解压目录；替换旧作品时先停止展示器，并完整替换 content，避免残留旧素材。',
  '接入支持 MCP 的外部 AI 宿主时，命令为系统 cmd.exe，参数为 /d /c 与本包 mcp.cmd 的完整路径。启动后需按插件 Skill 配对；真实宿主加载尚未验证。',
  '本地编辑与展示可离线运行；外部 AI 宿主自行配置。图片/视频生成当前只有明确标注的测试适配器。', '',
  'C 编程关卡的真实执行器需独立配置和启动；包内没有 C 编译服务。HTML 安装当前只支持单个入口文件，优先使用自包含程序。', '',
  '可选安装：install.cmd 复制到本机用户目录并创建快捷方式。更新保留 data/。便携使用无需安装。',
  '卸载便携副本：停止编辑器、备份 data/workspace/ 后删除该文件夹。', '',
].join('\n'));
writeFileSync(path.join(outDir, 'manifest.json'), `${JSON.stringify({ kind: 'shiguang-studio-portable', builtAt: new Date().toISOString(), sourceVersion: pkg.version, nodeRequirement: '>=22.18', bundledNode: nodeVersion, platform: 'win-x64', entries: { server: 'app/server/src/main.ts', editor: 'app/dist/studio-live.html', player: 'app/dist-player/player.html', plugin: 'app/plugin/studio-author', launcher: 'studio.cmd', runtime: 'runtime/node.exe', stop: '停止编辑器.cmd', mcp: 'mcp.cmd', install: 'install.mjs', installCmd: 'install.cmd', uninstall: 'uninstall.mjs' }, files: [] }, null, 2)}\n`);
const manifest = refreshManifest(outDir);
console.log(`制作包：${outDir}（${manifest.fileCount} 文件，${(manifest.totalBytes / 1048576).toFixed(2)} MiB；Node ${nodeVersion}）`);
if (process.argv.includes('--zip')) console.log(`压缩包：${zipDirectory(outDir)}`);
