// 针对最终 ZIP 的实际解压/双击入口/浏览器验收。仅开发验收依赖 Playwright。
import { chromium, expect } from '@playwright/test';
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { argValue } from './studio-lib.mjs';
import { copyTree, sha256, walk } from './studio-portable-lib.mjs';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
if (process.argv.includes('--help')) {
  console.log('Usage: node scripts/studio-portable-check.mjs [--release-dir DIR] [--out DIR] [--with-demo]\n--release-dir defaults to release; both input and output must remain within project subdirectories. ZIPs are read only; runtime and editor changes occur in an isolated extraction.');
  process.exit(0);
}
for (const flag of ['--release-dir', '--out']) {
  const index = process.argv.indexOf(flag);
  if (index >= 0 && (!process.argv[index + 1] || process.argv[index + 1].startsWith('--'))) throw new Error(`${flag} 需要目录参数`);
}
function within(parent, target) {
  const relative = path.relative(parent, target);
  return relative && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}
const version = JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8')).version;
const withDemo = process.argv.includes('--with-demo');
const releaseDir = path.resolve(ROOT, argValue(process.argv, '--release-dir', 'release'));
if (!within(ROOT, releaseDir) || !existsSync(releaseDir) || !statSync(releaseDir).isDirectory() || !within(realpathSync(ROOT), realpathSync(releaseDir))) throw new Error('待验 ZIP 必须位于项目内的实际交付子目录，请检查 --release-dir');
function archiveFor(kind) {
  const suffix = kind === 'Editor' ? '-export' : '';
  const name = kind === 'Player' && withDemo ? 'Player-Demo' : kind;
  return path.join(releaseDir, `ShiguangStudio-${name}-${version}-win-x64${suffix}.zip`);
}
for (const kind of ['Editor', 'Player']) {
  const archive = archiveFor(kind);
  if (!existsSync(archive) || !statSync(archive).isFile() || !within(realpathSync(releaseDir), realpathSync(archive))) throw new Error(`待验交付 ZIP 不存在或路径越界：${archive}`);
}
const out = path.resolve(ROOT, argValue(process.argv, '--out', `artifacts/studio-review/portable-${Date.now()}`));
if (!within(ROOT, out)) throw new Error('验收输出目录必须位于项目内子目录');
let ancestor = out;
while (!existsSync(ancestor)) ancestor = path.dirname(ancestor);
if (realpathSync(ancestor) !== realpathSync(ROOT) && !within(realpathSync(ROOT), realpathSync(ancestor))) throw new Error('验收输出目录解析到项目范围之外');
if (existsSync(out)) throw new Error('验收输出目录已经存在，请使用新目录');
mkdirSync(out, { recursive: true });
mkdirSync(path.join(ROOT, '.runtime'), { recursive: true });
const extraction = mkdtempSync(path.join(ROOT, '.runtime', '便携包解压验收 '));
const editor = path.join(extraction, '编辑器 副本');
const player = path.join(extraction, '展示器 副本');
const powershell = path.join(process.env.SystemRoot, 'System32/WindowsPowerShell/v1.0/powershell.exe');
const cmd = process.env.ComSpec ?? path.join(process.env.SystemRoot, 'System32/cmd.exe');
const env = { ...process.env, PATH: path.join(process.env.SystemRoot, 'System32'), NODE_OPTIONS: '', NODE_PATH: '' };
const results = [];
let browser;
const report = { startedAt: new Date().toISOString(), releaseDir: path.relative(ROOT, releaseDir).replace(/\\/g, '/'), archives: [], extraction, globalNodeInPath: false, checks: results };
function runBatch(directory, filename, args = []) {
  const result = spawnSync(cmd, ['/d', '/c', filename, ...args], { cwd: directory, env, windowsHide: true, encoding: 'utf8', timeout: 30000 });
  if (result.status !== 0) throw new Error(`启动脚本失败：${filename}\n${result.stdout}\n${result.stderr}`);
  return JSON.parse(readFileSync(path.join(directory, 'data/launcher-state.json'), 'utf8'));
}
async function check(name, action) {
  try { await action(); results.push({ name, status: 'passed' }); console.log(`PASS ${name}`); }
  catch (error) { results.push({ name, status: 'failed', message: error.message }); throw error; }
}
try {
  for (const [kind, directory] of [['Editor', editor], ['Player', player]]) {
    const archive = archiveFor(kind);
    report.archives.push({ path: path.relative(ROOT, archive).replace(/\\/g, '/'), bytes: readFileSync(archive).length, sha256: sha256(readFileSync(archive)) });
    execFileSync(powershell, ['-NoProfile', '-Command', 'Add-Type -AssemblyName System.IO.Compression.FileSystem; [IO.Compression.ZipFile]::ExtractToDirectory($env:STUDIO_CHECK_ZIP, $env:STUDIO_CHECK_DIR)'], { windowsHide: true, env: { ...process.env, STUDIO_CHECK_ZIP: archive, STUDIO_CHECK_DIR: directory } });
    await check(`${kind} ZIP 解压、全部文件 SHA256 与清单一致`, async () => {
      const manifest = JSON.parse(readFileSync(path.join(directory, 'manifest.json'), 'utf8'));
      expect(walk(directory).length).toBe(manifest.fileCount + 1);
      for (const file of manifest.files) expect(sha256(readFileSync(path.join(directory, file.rel))), file.rel).toBe(file.sha256);
    });
  }
  await check(withDemo ? '示例模式编辑器 ZIP 包含首启工程' : '默认编辑器 ZIP 无首启示例、工程内容或用户工作区', async () => {
    if (withDemo) expect(existsSync(path.join(editor, 'seed-workspace/projects.json'))).toBe(true);
    else {
      expect(existsSync(path.join(editor, 'seed-workspace'))).toBe(false);
      expect(existsSync(path.join(editor, 'data/workspace'))).toBe(false);
    }
  });
  if (!withDemo) await check('空白展示器 ZIP 不含作品内容、样例剧情或媒体素材', async () => {
    expect(existsSync(path.join(player, 'content/content.json'))).toBe(false);
    expect(existsSync(path.join(player, '样例说明.txt'))).toBe(false);
    expect(walk(path.join(player, 'content'))).toEqual(['放置作品说明.txt']);
    expect(JSON.parse(readFileSync(path.join(player, 'manifest.json'), 'utf8')).contentIncluded).toBe(false);
  });
  let editorState;
  let playerState;
  await check('编辑器 CMD 在无全局 Node 的 PATH、中文空格目录中启动', async () => { editorState = runBatch(editor, '启动编辑器.cmd', ['--no-browser']); });
  await check('展示器 CMD 在无全局 Node 的 PATH、中文空格目录中启动', async () => { playerState = runBatch(player, '启动展示器.cmd', ['--no-browser']); });
  browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  const external = [];
  let emptyPhase = !withDemo;
  let publishedDirectory = '';
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('response', (response) => {
    if (emptyPhase && response.status() === 404 && response.url() === `${playerState.origin}/content/content.json`) return;
    if (response.status() >= 400) errors.push(`HTTP ${response.status()} ${response.url()}`);
  });
  context.on('request', (request) => { if (/^https?:/.test(request.url()) && new URL(request.url()).hostname !== '127.0.0.1') external.push(request.url()); });
  const changedText = '这是解压后在编辑器里保存的对白。';
  const firstText = '这是从空白编辑器写下的第一句对白。';
  const secondText = '这是作者通过界面写下的第二句对白。';
  const endingTitle = '从空白写完第一部作品';
  if (!withDemo) await check('空白展示器启动提示放入作品，开始与继续按钮禁用', async () => {
    await page.goto(playerState.url);
    await expect(page.getByTestId('empty-player')).toContainText('content');
    await expect(page.getByTestId('start')).toBeDisabled();
    await expect(page.getByTestId('continue')).toBeDisabled();
    await page.screenshot({ path: path.join(out, 'player-empty.png'), fullPage: true });
  });
  await check(withDemo ? '示例编辑器连接后台、修改对白并持久保存' : '空编辑器欢迎页→界面新建作品→写对白和结局→持久保存', async () => {
    await page.goto(editorState.url);
    await expect(page.getByTestId('connection')).toContainText('后台已连接');
    if (!withDemo) {
      const workspace = path.join(editor, 'data/workspace');
      const registryPath = path.join(workspace, 'projects.json');
      if (existsSync(registryPath)) expect(JSON.parse(readFileSync(registryPath, 'utf8')).projects).toEqual([]);
      await expect(page.getByTestId('welcome')).toBeVisible();
      await expect(page.getByTestId('export-project')).toBeDisabled();
      await expect(page.getByTestId('preview-project')).toBeDisabled();
      await expect(page.getByTestId('validation')).not.toBeVisible();
      await page.screenshot({ path: path.join(out, 'editor-empty.png'), fullPage: true });
      await page.getByTestId('new-project').click();
      await page.getByTestId('structure-name').fill('解压后从零制作的作品');
      await page.getByTestId('structure-save').click();
      await expect(page.getByTestId('welcome')).not.toBeVisible();
      await expect(page.getByTestId('outline')).toContainText('第一章');
      await expect(page.getByTestId('outline')).toContainText('开场');
      report.authoredProjectId = await page.getByTestId('project-select').inputValue();
      const addEvent = async (type, configure) => {
        const count = await page.getByTestId('event-list').locator('li').count();
        await page.getByTestId('insert-type').selectOption(type);
        await page.getByTestId('insert-event').click();
        await expect(page.getByTestId('submit-draft')).toBeVisible();
        await configure();
        await page.getByTestId('submit-draft').click();
        await expect(page.getByTestId('cancel-draft')).not.toBeVisible();
        await expect(page.getByTestId('event-list').locator('li')).toHaveCount(count + 1);
      };
      await addEvent('dialogue', async () => { await page.getByTestId('param-text').fill(firstText); });
      await addEvent('dialogue', async () => { await page.getByTestId('param-text').fill(secondText); });
      await addEvent('ending', async () => {
        await page.getByTestId('param-endingId').fill('完成');
        await page.getByTestId('param-title').fill(endingTitle);
      });
    }
    await page.getByTestId('event-list').getByRole('button').filter({ hasText: withDemo ? '雨点敲在旧塔的铁门上。' : firstText }).first().click();
    await page.getByTestId('start-edit').click();
    await page.getByTestId('param-text').fill(changedText);
    await expect(page.getByTestId('export-project')).toBeDisabled();
    await page.getByTestId('submit-draft').click();
    await expect(page.getByTestId('message')).toContainText('已保存');
    await page.screenshot({ path: path.join(out, 'editor.png'), fullPage: true });
  });
  await check('重启后保留作者作品与修改', async () => {
    const stop = spawnSync(cmd, ['/d', '/c', '停止编辑器.cmd'], { cwd: editor, env, windowsHide: true, timeout: 30000 });
    expect(stop.status).toBe(0);
    editorState = runBatch(editor, '启动编辑器.cmd', ['--no-browser']);
    await page.goto(editorState.url);
    await expect(page.getByTestId('connection')).toContainText('后台已连接');
    await expect(page.getByTestId('event-list')).toContainText(changedText);
  });
  await check('编辑器界面导出已保存作品，结果给出交付目录与操作说明', async () => {
    await page.getByTestId('export-project').click();
    await expect(page.getByTestId('export-result')).toContainText('作品已导出');
    const relative = await page.getByTestId('export-directory').innerText();
    const directory = path.join(editor, 'data/workspace', relative);
    publishedDirectory = directory;
    const exported = JSON.parse(readFileSync(path.join(directory, 'manifest.json'), 'utf8'));
    const savedRevision = await page.getByTestId('revision').innerText();
    expect(savedRevision).toContain(`修订 ${exported.revision}`);
    expect(exported.playerIncluded).toBe(true);
    expect(exported.portableIncluded).toBe(true);
    expect(exported.missingAssets).toEqual([]);
    expect(exported.missingLevels).toEqual([]);
    expect(existsSync(path.join(directory, '启动展示器.cmd'))).toBe(true);
    expect(existsSync(path.join(directory, 'runtime/node.exe'))).toBe(true);
    expect(readFileSync(path.join(directory, 'content/content.json'), 'utf8')).toContain(changedText);
    if (withDemo) expect(existsSync(path.join(directory, 'content/levels/demo/theme.css'))).toBe(true);
    else {
      const content = JSON.parse(readFileSync(path.join(directory, 'content/content.json'), 'utf8'));
      expect(content.projectId).toBe(report.authoredProjectId);
      expect(content.scenes.flatMap((scene) => scene.events).map((event) => event.type)).toEqual(['dialogue', 'dialogue', 'ending']);
      expect(content.scenes.flatMap((scene) => scene.events).find((event) => event.type === 'ending').params.title).toBe(endingTitle);
    }
    expect(exported.sensitiveHits).toEqual([]);
    await page.screenshot({ path: path.join(out, 'editor-export.png'), fullPage: true });
  });
  if (!withDemo) await check('只复制导出的整个 content 文件夹，空白展示器即可读取作者作品', async () => {
    copyTree(path.join(publishedDirectory, 'content'), path.join(player, 'content'));
    expect(readFileSync(path.join(player, 'content/content.json'), 'utf8')).toContain(changedText);
    emptyPhase = false;
  });
  await check(withDemo ? '示例展示器剧情：对白、选择、嵌套关卡资源、三盏灯、结局' : '实际展示器运行界面从零写出的对白与结局', async () => {
    await page.goto(playerState.url);
    await page.getByTestId('start').click();
    await expect(page.getByTestId('dialogue')).toContainText(withDemo ? '雨点敲在旧塔的铁门上。' : changedText);
    await page.getByTestId('advance').click();
    if (withDemo) {
      await page.getByTestId('choice-0').click();
      await expect(page.getByTestId('dialogue')).toContainText('门后是向上的石阶。');
      await page.getByTestId('advance').click();
      const frame = page.frameLocator('[data-testid="level-frame"]');
      await expect(frame.locator('body')).toHaveCSS('background-color', 'rgb(21, 32, 49)');
      for (let n = 1; n <= 3; n++) await frame.locator(`#lantern-${n}`).click();
      await expect(page.getByTestId('ending')).toContainText('塔顶');
    } else {
      await expect(page.getByTestId('dialogue')).toContainText(secondText);
      await page.getByTestId('advance').click();
      await expect(page.getByTestId('ending')).toContainText(endingTitle);
    }
    await page.screenshot({ path: path.join(out, 'player-ending.png'), fullPage: true });
  });
  await check('展示器保存、刷新与继续游戏', async () => {
    await page.goto(playerState.url);
    await page.getByTestId('start').click();
    await page.getByTestId('advance').click();
    await page.getByTestId('save').click();
    await expect(page.locator('#player-status')).toContainText('已保存');
    await page.reload();
    await page.getByTestId('continue').click();
    if (withDemo) await expect(page.getByTestId('choices')).toContainText('进塔');
    else await expect(page.getByTestId('dialogue')).toContainText(secondText);
  });
  await check('展示器控制密钥、日志与运行时不通过 HTTP 提供', async () => {
    expect((await fetch(`${playerState.origin}/data/launcher-state.json`)).status).toBe(403);
    expect((await fetch(`${playerState.origin}/runtime/node.exe`)).status).toBe(403);
    expect((await fetch(`${playerState.origin}/__portable/status`)).status).toBe(403);
    expect((await fetch(`${playerState.origin}/content/content.json`, { headers: { range: 'bytes=0-15' } })).status).toBe(206);
  });
  await check('运行期间无外部资源请求、无浏览器错误', async () => { expect(external).toEqual([]); expect(errors).toEqual([]); });
  await check('验收过程保持原始交付 ZIP 散列不变', async () => {
    for (const archive of report.archives) expect(sha256(readFileSync(path.join(ROOT, archive.path)))).toBe(archive.sha256);
  });
} catch (error) { console.error(error.stack); process.exitCode = 1; }
finally {
  await browser?.close();
  for (const directory of [editor, player]) if (existsSync(path.join(directory, 'launch.mjs'))) {
    const stopped = spawnSync(path.join(directory, 'runtime/node.exe'), [path.join(directory, 'launch.mjs'), 'stop'], { cwd: directory, env, windowsHide: true, encoding: 'utf8', timeout: 30000 });
    results.push({ name: `${path.basename(directory)} 停止服务`, status: stopped.status === 0 ? 'passed' : 'failed' });
    if (stopped.status !== 0) process.exitCode = 1;
  }
  report.finishedAt = new Date().toISOString();
  report.summary = { passed: results.filter((r) => r.status === 'passed').length, failed: results.filter((r) => r.status === 'failed').length };
  writeFileSync(path.join(out, 'portable-check.json'), `${JSON.stringify(report, null, 2)}\n`);
  writeFileSync(path.join(out, 'artifacts.sha256'), walk(out).map((rel) => `${sha256(readFileSync(path.join(out, rel)))}  ${rel}`).join('\n') + '\n');
  console.log(JSON.stringify(report.summary));
}
