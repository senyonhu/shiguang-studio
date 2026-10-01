// 默认构建空白编辑器与展示器；--with-demo 才通过独立后台事务制作首启示例。
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startService } from '../server/src/main.ts';
import { exportProject } from '../server/src/publisher/service.ts';
import { installPlayerPortable } from '../server/src/publisher/portable.ts';
import { buildStory, LANTERN_LEVEL_HTML } from '../tests/studio/helpers/story.ts';
import { copyTree, refreshManifest, zipDirectory } from './studio-portable-lib.mjs';
import { argValue } from './studio-lib.mjs';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const version = JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8')).version;
const releaseDir = path.resolve(ROOT, argValue(process.argv, '--out', 'release'));
if (!releaseDir.startsWith(ROOT + path.sep)) throw new Error('交付物必须输出到项目内子目录');
const withDemo = process.argv.includes('--with-demo');
const editor = path.join(releaseDir, `ShiguangStudio-Editor-${version}-win-x64-export`);
const player = path.join(releaseDir, `ShiguangStudio-Player${withDemo ? '-Demo' : ''}-${version}-win-x64`);
for (const output of [editor, player, `${editor}.zip`, `${player}.zip`]) if (existsSync(output)) throw new Error(`交付物已存在，不覆盖：${output}`);
const result = spawnSync(process.execPath, ['scripts/studio-package.mjs', '--out', editor], { cwd: ROOT, stdio: 'inherit' });
if (result.status !== 0) throw new Error('制作包构建失败');
let sampleProjectId = null;
if (withDemo) {
  const workspace = mkdtempSync(path.join(os.tmpdir(), 'studio-portable-demo-'));
  const service = await startService({ workspaceRoot: workspace, host: '127.0.0.1', port: 0, printDiscovery: false, initPairing: false, uiOrigins: [] });
  try {
    const pairingKey = JSON.parse(readFileSync(path.join(workspace, '.studio-service/credentials/pairing.key'), 'utf8')).pairingKey;
    const session = await fetch(`${service.url}/v1/sessions`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ pairingKey, clientName: 'portable-demo-builder', scopes: ['read', 'edit', 'preview', 'export'] }) }).then((r) => r.json());
    if (!session.token) throw new Error('示例构建配对失败');
    const story = await buildStory(service.url, session.token, workspace);
    const projectDir = path.join(workspace, story.projectId);
    const demoDeps = path.join(projectDir, 'levels/demo');
    mkdirSync(demoDeps, { recursive: true });
    writeFileSync(path.join(demoDeps, 'theme.css'), 'body{background:#152031;color:#edf3ff;font:18px system-ui;padding:30px;text-align:center}button{font:inherit;padding:18px;margin:10px;border-radius:12px;background:#283e5c;color:white;border:1px solid #7fb2ff}button[data-lit="true"]{background:#ad852d;color:#fff}');
    writeFileSync(path.join(demoDeps, 'info.json'), JSON.stringify({ title: '点亮三盏灯', example: true }));
    writeFileSync(path.join(projectDir, `levels/${story.levelId}.html`), LANTERN_LEVEL_HTML.replace('</head>', '<link rel="stylesheet" href="demo/theme.css"></head>'));
    // 仅保留示例和当前状态，不把服务凭据、历史导出或生成任务复制进交付物。
    const seed = path.join(editor, 'seed-workspace');
    mkdirSync(seed, { recursive: true });
    cpSync(path.join(workspace, 'projects.json'), path.join(seed, 'projects.json'));
    copyTree(projectDir, path.join(seed, story.projectId), (rel) => !rel.startsWith('exports/') && (!rel.startsWith('.studio/') || rel === '.studio/state.json'));
    sampleProjectId = story.projectId;
    const exported = exportProject({ workspaceRoot: workspace, repoRoot: ROOT, portableRuntimeRoot: path.join(editor, 'runtime') }, story.projectId, { revision: story.revision });
    if (!exported.playerIncluded || !exported.portableIncluded || exported.sensitiveHits.length || exported.missingAssets.length || exported.missingLevels.length) throw new Error('展示样例不满足发布完整性');
    copyTree(path.join(workspace, exported.directory), player);
  } finally { await service.close(); }
} else {
  copyTree(path.join(ROOT, 'dist-player'), player);
  mkdirSync(path.join(player, 'content'), { recursive: true });
  writeFileSync(path.join(player, 'content/放置作品说明.txt'), '将编辑器导出的整个 content 文件夹放到展示器目录，替换本文件夹。不要直接复制作者工程目录。\r\n');
  installPlayerPortable(player, path.join(editor, 'runtime'), path.join(ROOT, 'scripts/studio-portable-launch.mjs'));
  writeFileSync(path.join(player, 'manifest.json'), JSON.stringify({ kind: 'shiguang-studio-player-empty', builtAt: new Date().toISOString(), sourceVersion: version, platform: 'win-x64', contentIncluded: false, files: [] }, null, 2));
}
mkdirSync(path.join(player, 'licenses'), { recursive: true });
cpSync(path.join(ROOT, 'node_modules/vue/LICENSE'), path.join(player, 'licenses/Vue-LICENSE.txt'));
mkdirSync(path.join(player, 'docs'), { recursive: true });
for (const filename of ['USER_GUIDE.zh-CN.md', 'AUDIT_2026-10-01.md', 'studio-backend.md']) cpSync(path.join(ROOT, 'docs', filename), path.join(player, 'docs', filename));
copyTree(path.join(ROOT, 'docs/images'), path.join(player, 'docs/images'));
const playerReadme = path.join(player, 'README.txt');
writeFileSync(playerReadme, `${readFileSync(playerReadme, 'utf8')}\r\n完整中文说明：docs/USER_GUIDE.zh-CN.md（独立展示器、玩家操作与存档）。\r\n公开源码与更新：https://github.com/senyonhu/shiguang-studio\r\n`);
if (withDemo) writeFileSync(path.join(player, '样例说明.txt'), '雨夜旧塔：展示器样例。包含对白、选择、三盏灯交互关卡、结局与存档。素材为简单占位背景，不是正式作品。\r\n');
const editorManifest = refreshManifest(editor);
const playerManifest = refreshManifest(player);
const archives = [zipDirectory(editor), zipDirectory(player)];
console.log(JSON.stringify({ archives, editorFiles: editorManifest.fileCount, playerFiles: playerManifest.fileCount, sampleProjectId }, null, 2));
