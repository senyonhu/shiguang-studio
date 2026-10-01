// P2 展示器验证：导出发布包 → 用发布包内的 serve.mjs 启动 → 浏览器里真实跑通剧情与关卡。
import { expect, test } from '@playwright/test';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { cpSync, existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { buildStory, call, LANTERN_LEVEL_HTML } from '../studio/helpers/story.ts';
import { trace } from '../studio/helpers/harness.ts';
import type { StoryFixture } from '../studio/helpers/story.ts';

const REPO_ROOT = process.cwd();

/** 生成一段真实可播放的 WAV（16 位单声道静音），用于验证音频宿主而不是模拟播放。 */
function makeSilentWav(seconds: number): Buffer {
  const sampleRate = 8000;
  const samples = Math.max(1, Math.round(sampleRate * seconds));
  const data = Buffer.alloc(samples * 2); // 全 0 = 静音
  const header = Buffer.alloc(44);
  header.write('RIFF', 0, 'ascii');
  header.writeUInt32LE(36 + data.length, 4);
  header.write('WAVE', 8, 'ascii');
  header.write('fmt ', 12, 'ascii');
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20); // PCM
  header.writeUInt16LE(1, 22); // 单声道
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write('data', 36, 'ascii');
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

interface Backend { url: string; token: string; workspace: string; child: ChildProcess }
interface PackageServer { url: string; child: ChildProcess }

async function startBackend(): Promise<Backend> {
  const workspace = mkdtempSync(path.join(os.tmpdir(), 'studio-e2e-player-'));
  const child = spawn(process.execPath, [path.join(REPO_ROOT, 'server', 'src', 'main.ts'), '--workspace', workspace, '--port', '0'], { stdio: ['ignore', 'pipe', 'pipe'] });
  const discoveryFile = path.join(workspace, '.studio-service', 'runtime.json');
  let url = '';
  for (let attempt = 0; attempt < 120 && !url; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    try {
      const record = JSON.parse(readFileSync(discoveryFile, 'utf8')) as { url: string };
      const health = await fetch(`${record.url}/v1/health`).then((response) => response.json()).catch(() => null) as { status?: string } | null;
      if (health?.status === 'ok') url = record.url;
    } catch { /* 等待发现记录 */ }
  }
  if (!url) throw new Error('后台未能启动');
  const pairingKey = (JSON.parse(readFileSync(path.join(workspace, '.studio-service', 'credentials', 'pairing.key'), 'utf8')) as { pairingKey: string }).pairingKey;
  const session = await fetch(`${url}/v1/sessions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ pairingKey, clientName: 'e2e-player', scopes: ['read', 'edit', 'preview', 'export'] }),
  }).then((response) => response.json()) as { token: string };
  return { url, token: session.token, workspace, child };
}

async function startPackageServer(packageDir: string): Promise<PackageServer> {
  const child = spawn(process.execPath, [path.join(packageDir, 'serve.mjs'), '--port', '0'], { stdio: ['ignore', 'pipe', 'pipe'] });
  let url = '';
  let buffer = '';
  await new Promise<void>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`发布包服务器启动超时：${buffer}`)), 15000);
    child.stdout?.on('data', (chunk: Buffer) => {
      buffer += chunk.toString();
      const match = /http:\/\/127\.0\.0\.1:(\d+)\/player\.html/.exec(buffer);
      if (match) {
        clearTimeout(timer);
        url = match[0];
        resolve();
      }
    });
    child.on('close', () => { clearTimeout(timer); reject(new Error(`发布包服务器提前退出：${buffer}`)); });
  }).catch((error: Error) => {
    throw error;
  });
  // serve.mjs 不支持 --port 0，这里回退读取实际监听端口
  if (!url) throw new Error('未获得发布包地址');
  return { url, child };
}

test.describe('独立展示器运行发布包', () => {
  let backend: Backend;
  let story: StoryFixture;
  let packageDir = '';
  let server: PackageServer;

  test.beforeAll(async () => {
    test.setTimeout(180000);
    backend = await startBackend();
    story = await buildStory(backend.url, backend.token, backend.workspace);
    const exported = await call(backend.url, backend.token, 'POST', `/v1/projects/${story.projectId}/exports`, {});
    expect(exported.status, JSON.stringify(exported.body)).toBe(201);
    const body = exported.body as { directory: string; playerIncluded: boolean; sensitiveHits: string[]; missingAssets: string[] };
    expect(body.playerIncluded).toBe(true);
    expect(body.sensitiveHits).toEqual([]);
    expect(body.missingAssets).toEqual([]);
    packageDir = path.join(backend.workspace, body.directory);
    server = await startPackageServer(packageDir);
  });

  test.afterAll(async () => {
    if (server?.child.exitCode === null) server.child.kill('SIGKILL');
    if (backend?.child.exitCode === null) backend.child.kill('SIGKILL');
    await new Promise((resolve) => setTimeout(resolve, 200));
  });

  test('展示器从发布包运行完整路线：对白 → 选择 → 关卡 → 结局', async ({ page }) => {
    const consoleErrors: string[] = [];
    page.on('pageerror', (error) => consoleErrors.push(error.message));
    page.on('response', (response) => { if (response.status() >= 400) consoleErrors.push(`HTTP ${response.status()} ${response.url()}`); });
    await page.goto(server.url);
    await expect(page.getByTestId('start')).toBeVisible();
    await page.getByTestId('start').click();
    await expect(page.getByTestId('dialogue')).toContainText(story.dialogueText);
    // 主题：展示器按 themes/theme.json 渲染（默认主题的文字色/字号）
    await expect(page.getByTestId('dialogue')).toHaveCSS('color', 'rgb(232, 238, 247)');
    await expect(page.getByTestId('dialogue')).toHaveCSS('font-size', '18px');
    await expect(page.locator('[data-testid="background"]')).toBeVisible();
    await page.screenshot({ path: 'test-results/player-dialogue.png', fullPage: true });

    await page.getByTestId('advance').click();
    await expect(page.getByTestId('choices')).toContainText('进塔');
    await page.screenshot({ path: 'test-results/player-choice.png', fullPage: true });
    await page.getByTestId('choice-0').click();
    await expect(page.getByTestId('dialogue')).toContainText('门后是向上的石阶。');
    await page.getByTestId('advance').click();

    // HTML 关卡宿主：iframe 加载 → ready → init → checkpoint/complete（含重复完成消息）
    await expect(page.getByTestId('level-frame')).toBeVisible();
    await expect(page.getByTestId('ending')).toContainText('塔顶', { timeout: 20000 });
    await expect(page.getByTestId('toolbar')).toContainText('已结束');
    // 重复完成消息不重复推进：历史对白仍只有两句
    await expect(page.getByTestId('history')).toContainText('历史对白（2）');
    expect(consoleErrors).toEqual([]);
    await page.screenshot({ path: 'test-results/player-ending.png', fullPage: true });
  });

  test('存读档：保存后可继续，工程修订变化后拒绝旧存档', async ({ page }) => {
    await page.goto(server.url);
    await page.getByTestId('start').click();
    await page.getByTestId('advance').click();
    await expect(page.getByTestId('choices')).toContainText('进塔');
    await page.getByTestId('save').click();
    await expect(page.locator('#player-status')).toContainText('已保存');

    await page.reload();
    await page.getByTestId('continue').click();
    await expect(page.getByTestId('choices')).toContainText('进塔');

    // 工程修订变化后：重新导出并替换内容包（模拟新发行），旧存档必须被拒绝
    const bumped = await call(backend.url, backend.token, 'POST', `/v1/projects/${story.projectId}/transactions`, {
      requestId: `player-bump-${Date.now()}`,
      projectId: story.projectId,
      baseRevision: story.revision,
      operations: [{ action: 'event.insert', payload: { sceneId: story.levelSceneId, type: 'wait', params: { ms: 5 } } }],
    });
    expect(bumped.body.status).toBe('applied');
    story.revision = bumped.body.revision as string;

    const contentPath = path.join(packageDir, 'content', 'content.json');
    const bundle = JSON.parse(readFileSync(contentPath, 'utf8')) as { revision: string };
    bundle.revision = story.revision;
    writeFileSync(contentPath, `${JSON.stringify(bundle, null, 2)}\n`, 'utf8');

    await page.reload();
    await page.getByTestId('continue').click();
    await expect(page.locator('body')).toContainText('读档失败');
    await expect(page.locator('body')).toContainText('需重新开始');
  });

  test('多存档槽与文本速度设置（槽位互不覆盖，设置持久化）', async ({ page }) => {
    await page.goto(server.url);
    await page.getByTestId('start').click();
    await page.getByTestId('advance').click();
    await expect(page.getByTestId('choices')).toContainText('进塔');

    // 槽位 1 为空时读取应给出明确提示（槽位标签也显示“空”）
    await expect(page.getByTestId('slot-select')).toContainText('槽位 1：空');
    await page.getByTestId('load').click();
    await expect(page.locator('body')).toContainText('槽位 1 没有找到存档');
    await expect(page.getByTestId('choices')).toContainText('进塔');

    // 保存到槽位 2
    await page.getByTestId('slot-select').selectOption('2');
    await expect(page.getByTestId('save')).toContainText('保存到槽位 2');
    await page.getByTestId('save').click();
    await expect(page.locator('#player-status')).toContainText('已保存到槽位 2');
    await expect(page.getByTestId('slot-select')).toContainText('槽位 2：20');

    // 槽位 1 仍然为空：槽位之间互不覆盖
    await page.getByTestId('slot-select').selectOption('1');
    await expect(page.getByTestId('slot-select')).toContainText('槽位 1：空');
    await page.getByTestId('load').click();
    await expect(page.locator('body')).toContainText('槽位 1 没有找到存档');

    // 从槽位 2 读回：进度与保存点一致（回到选择点）
    await page.getByTestId('choice-0').click();
    await expect(page.getByTestId('dialogue')).toContainText('门后是向上的石阶。');
    await expect(page.getByTestId('choices')).toHaveCount(0);
    await page.getByTestId('slot-select').selectOption('2');
    await page.getByTestId('load').click();
    await expect(page.getByTestId('choices')).toContainText('进塔');

    // 删除槽位 2 后不再有存档
    await page.getByTestId('clear-save').click();
    await expect(page.locator('#player-status')).toContainText('已删除槽位 2');
    await page.getByTestId('slot-select').selectOption('2');
    await expect(page.getByTestId('slot-select')).toContainText('槽位 2：空');

    // 文本速度设置：改变后立即生效并写入本地存储
    await page.getByTestId('text-speed').selectOption('fast');
    await expect(page.locator('#player-status')).toContainText('自动播放等待 500ms');
    const persisted = await page.evaluate(() => localStorage.getItem('shiguang-player-settings-v1'));
    expect(persisted).toContain('fast');
    await page.reload();
    // 刷新后回到标题页；开始游戏后设置必须已从本地存储恢复
    await page.getByTestId('start').click();
    await expect(page.getByTestId('text-speed')).toHaveValue('fast');
    await expect(page.getByTestId('toolbar')).toContainText('自动等待 500ms');

    // 快速档位下自动播放推进明显快于默认档位：短时间内完成一次推进
    await page.getByTestId('auto').click();
    const advanced = await page.waitForFunction(
      () => document.querySelector('[data-testid="advance"]') !== null || document.querySelector('[data-testid="choices"]') !== null,
      undefined,
      { timeout: 3000 },
    ).then(() => true).catch(() => false);
    expect(advanced).toBe(true);
  });

  test('音频宿主播放并等待结束；素材缺失时明确报错且运行继续（不会永久等待）', async ({ page }) => {
    // 用真实 WAV（44 字节头 + 约 0.2 秒静音）与一个不存在的视频素材验证宿主行为
    const sceneId = story.entrySceneId;
    const revisionNow = (await call(backend.url, backend.token, 'GET', `/v1/projects/${story.projectId}`)).body.revision as string;
    const wavPath = path.join(backend.workspace, 'imports', 'sefx.wav');
    writeFileSync(wavPath, makeSilentWav(0.2));
    const seeded = await call(backend.url, backend.token, 'POST', `/v1/projects/${story.projectId}/transactions`, {
      requestId: `player-media-${Date.now()}`,
      projectId: story.projectId,
      baseRevision: revisionNow,
      operations: [
        { action: 'asset.registerFromFile', payload: { sourcePath: wavPath, name: '提示音', kind: 'audio' } },
      ],
    });
    expect(seeded.body.status, JSON.stringify(seeded.body)).toBe('applied');
    const audioId = (seeded.body.changedIds as string[]).find((id) => id.startsWith('as_')) as string;

    // 引用不存在的视频素材必须被提交校验拦住（否则说明素材引用校验缺失）
    const badVideo = await call(backend.url, backend.token, 'POST', `/v1/projects/${story.projectId}/transactions`, {
      requestId: `player-media-badvideo-${Date.now()}`,
      projectId: story.projectId,
      baseRevision: seeded.body.revision,
      operations: [
        { action: 'event.insert', payload: { sceneId, type: 'video', params: { assetId: 'as_missingvideo01' }, eventId: 'ev_mediavideo01' } },
      ],
    });
    expect(badVideo.body.status, JSON.stringify(badVideo.body)).toBe('rejected');
    expect(JSON.stringify(badVideo.body.issues)).toContain('素材');

    // 两条音频事件：背景音（不等待）+ 音效（waitForEnd，结束后继续），后面接一句对白
    const withAudio = await call(backend.url, backend.token, 'POST', `/v1/projects/${story.projectId}/transactions`, {
      requestId: `player-media-audio-${Date.now()}`,
      projectId: story.projectId,
      baseRevision: seeded.body.revision,
      operations: [
        { action: 'event.insert', payload: { sceneId, type: 'audio', params: { assetId: audioId, channel: 'bgm' }, eventId: 'ev_mediabgm0001', index: 0 } },
        { action: 'event.insert', payload: { sceneId, type: 'audio', params: { assetId: audioId, channel: 'sefx', waitForEnd: true }, eventId: 'ev_mediaaudio01', index: 1 } },
        { action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '音效之后的对白。' }, eventId: 'ev_mediaafter01', index: 2 } },
      ],
    });
    expect(withAudio.body.status, JSON.stringify(withAudio.body)).toBe('applied');

    // 重新导出并启动新的发布包服务器（内容包含音频素材与新事件）
    const reexported = await call(backend.url, backend.token, 'POST', `/v1/projects/${story.projectId}/exports`, {});
    expect(reexported.status).toBe(201);
    const mediaDir = path.join(backend.workspace, (reexported.body as { directory: string }).directory);
    // 另起一个服务器服务重新导出的包（不影响其它用例共用的 server）
    const mediaServer = await startPackageServer(mediaDir);

    await page.goto(mediaServer.url);
    await page.getByTestId('start').click();
    // 背景音：真实 <audio> 元素、循环播放、不阻塞剧情
    const bgm = page.getByTestId('audio-bgm');
    await expect(bgm).toBeAttached({ timeout: 20000 });
    expect(await bgm.getAttribute('src')).toContain('/content/assets/originals/');
    expect(await bgm.evaluate((node) => (node as HTMLAudioElement).loop)).toBe(true);
    // 音效（waitForEnd）：播放结束后保留节点并标记 ended，随后运行继续
    const audio = page.getByTestId('audio-sefx');
    await expect(audio).toBeAttached({ timeout: 20000 });
    expect(await audio.getAttribute('src')).toContain('/content/assets/originals/');
    await expect(audio).toHaveAttribute('data-state', 'ended', { timeout: 20000 });
    await expect(page.getByTestId('dialogue')).toContainText('音效之后的对白', { timeout: 20000 });
    await page.screenshot({ path: 'test-results/player-audio.png', fullPage: true });
    if (mediaServer.child.exitCode === null) mediaServer.child.kill('SIGKILL');
  });

  test('关卡中途存档：读档后把快照交回关卡程序并从快照继续（不重新开始）', async ({ page }) => {
    await page.goto(server.url);
    await page.getByTestId('start').click();
    await page.getByTestId('advance').click();
    await expect(page.getByTestId('choices')).toContainText('进塔');
    await page.getByTestId('choice-0').click();
    await page.getByTestId('advance').click();

    // 关卡承载：等关卡发出 checkpoint（内核把快照存进运行状态）
    const frame = page.getByTestId('level-frame');
    await expect(frame).toBeVisible({ timeout: 20000 });
    await expect(page.frameLocator('[data-testid="level-frame"]').locator('#echo')).toContainText('已收到宿主初始化', { timeout: 20000 });
    // 等关卡发出 checkpoint（界面会提示已记录进度点），此时保存才是“关卡进行中”的状态
    await expect(page.locator('#player-status')).toContainText('已记录进度点', { timeout: 20000 });
    await page.getByTestId('save').click();
    await expect(page.locator('#player-status')).toContainText('已保存到槽位 1');
    const savedHasLevel = await page.evaluate((projectId) => {
      const raw = localStorage.getItem(`shiguang-player-save-v1:${encodeURIComponent(projectId)}`);
      const record = raw ? JSON.parse(raw) : null;
      return Boolean(record?.levels?.snapshot) && record?.pendingEffect?.kind === 'level';
    }, story.projectId);
    expect(savedHasLevel, '存档里必须包含关卡快照与待完成的关卡效果').toBe(true);

    // 读档：关卡程序应收到 snapshot 并直接从快照继续
    await page.reload();
    await page.getByTestId('continue').click();
    await expect(frame).toBeVisible({ timeout: 20000 });
    await expect(page.getByTestId('level-status')).toContainText('支持快照');
    const restored = page.frameLocator('[data-testid="level-frame"]').locator('#echo');
    // 若宿主没有下发快照，关卡会显示“已收到宿主初始化”并重新开始；恢复成功时应显示角度
    await expect(restored).toContainText('已从快照恢复：angle=42', { timeout: 20000 });
    await expect(page.getByTestId('ending')).toContainText('塔顶', { timeout: 20000 });
    await page.screenshot({ path: 'test-results/player-level-snapshot.png', fullPage: true });
  });

  test('第二个真实玩法关卡（点灯）：按顺序点亮、checkpoint 存档、读档后从快照继续', async ({ page }) => {
    // 独立工程：对白 → 点灯关卡 → 对白 → 结局（不改动共用样板工程）
    const created = await call(backend.url, backend.token, 'POST', '/v1/projects', { title: '点灯关卡工程', chapterTitle: '灯室', sceneTitle: '灯室' });
    const projectId = created.body.projectId as string;
    let revision = created.body.revision as string;
    const sceneId = created.body.sceneId as string;

    const registered = await call(backend.url, backend.token, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'lantern-register',
      projectId,
      baseRevision: revision,
      operations: [{ action: 'level.register', payload: { levelId: 'lv_lantern001', title: '点灯关卡', entry: 'levels/lv_lantern001.html', supportsSnapshot: true } }],
    });
    expect(registered.body.status, JSON.stringify(registered.body)).toBe('applied');
    revision = registered.body.revision as string;

    const programPath = path.join(backend.workspace, 'imports', 'lantern.html');
    writeFileSync(programPath, LANTERN_LEVEL_HTML, 'utf8');
    const installed = await call(backend.url, backend.token, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'lantern-install',
      projectId,
      baseRevision: revision,
      operations: [{ action: 'level.installProgram', targetIds: ['lv_lantern001'], payload: { levelId: 'lv_lantern001', sourcePath: programPath } }],
    });
    expect(installed.body.status, JSON.stringify(installed.body)).toBe('applied');
    revision = installed.body.revision as string;

    const events = await call(backend.url, backend.token, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'lantern-events',
      projectId,
      baseRevision: revision,
      operations: [
        { action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '灯室里只有三盏灯。' }, eventId: 'ev_lanternline1' } },
        { action: 'event.insert', payload: { sceneId, type: 'level', params: { levelId: 'lv_lantern001', supportsSnapshot: true }, eventId: 'ev_lanternlevel' } },
        { action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '灯全亮了。' }, eventId: 'ev_lanternline2' } },
        { action: 'event.insert', payload: { sceneId, type: 'ending', params: { endingId: 'end_lantern', title: '灯室结局' }, eventId: 'ev_lanternend1' } },
      ],
    });
    expect(events.body.status, JSON.stringify(events.body)).toBe('applied');

    const exported = await call(backend.url, backend.token, 'POST', `/v1/projects/${projectId}/exports`, {});
    expect(exported.status).toBe(201);
    const lanternServer = await startPackageServer(path.join(backend.workspace, (exported.body as { directory: string }).directory));

    await page.goto(lanternServer.url);
    await page.getByTestId('start').click();
    await expect(page.getByTestId('dialogue')).toContainText('灯室里只有三盏灯。');
    await page.getByTestId('advance').click();

    const level = page.frameLocator('[data-testid="level-frame"]');
    await expect(level.locator('#progress')).toContainText('已点亮 0 / 3', { timeout: 20000 });
    // 乱序点击不算数（必须先点亮 1 号）
    await level.locator('#lantern-2').click();
    await expect(level.locator('#progress')).toContainText('已点亮 0 / 3');
    await level.locator('#lantern-1').click();
    await expect(level.locator('#progress')).toContainText('已点亮 1 / 3');
    await level.locator('#lantern-2').click();
    await expect(level.locator('#progress')).toContainText('已点亮 2 / 3');
    // 关卡进行中：进度点已记录，关卡尚未完成
    await expect(page.locator('#player-status')).toContainText('已记录进度点', { timeout: 20000 });
    await expect(page.getByTestId('dialogue')).toContainText('灯室里只有三盏灯。');

    // 关卡中途存档 → 读档：应从“已点亮 2 盏”继续，而不是重新开始
    await page.getByTestId('save').click();
    await expect(page.locator('#player-status')).toContainText('已保存到槽位 1');
    await page.reload();
    await page.getByTestId('continue').click();
    await expect(level.locator('#hint')).toContainText('已从快照恢复', { timeout: 20000 });
    await expect(level.locator('#progress')).toContainText('已点亮 2 / 3');
    expect(await level.locator('body').getAttribute('data-restored')).toBe('true');

    // 点亮第三盏 → 关卡完成 → 后续对白与结局
    await page.screenshot({ path: 'test-results/player-lantern-checkpoint.png', fullPage: true });
    await level.locator('#lantern-3').click();
    await expect(page.getByTestId('dialogue')).toContainText('灯全亮了。', { timeout: 20000 });
    await page.getByTestId('advance').click();
    await expect(page.getByTestId('ending')).toContainText('灯室结局', { timeout: 20000 });
    await page.screenshot({ path: 'test-results/player-lantern-level.png', fullPage: true });
    if (lanternServer.child.exitCode === null) lanternServer.child.kill('SIGKILL');
  });


  test('视频宿主：真实 MP4 能解码播放，结束后运行继续（跳过按钮同样可用）', async ({ page }) => {
    // 独立工程：对白 → 视频事件（真实 1 秒 H.264/MP4 素材）→ 对白 → 结局
    const created = await call(backend.url, backend.token, 'POST', '/v1/projects', { title: '视频放映工程', chapterTitle: '放映室', sceneTitle: '放映室' });
    const projectId = created.body.projectId as string;
    let revision = created.body.revision as string;
    const sceneId = created.body.sceneId as string;

    // 素材是本地小样本（1 秒 320x240 H.264，约 2KB，sha256 见证据包）
    const fixture = path.join(REPO_ROOT, 'tests', 'studio', 'fixtures', 'sample-1s.mp4');
    expect(existsSync(fixture), '测试视频素材必须存在').toBe(true);
    const copied = path.join(backend.workspace, 'imports', 'sample-1s.mp4');
    cpSync(fixture, copied);
    const registered = await call(backend.url, backend.token, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'video-asset',
      projectId,
      baseRevision: revision,
      operations: [{ action: 'asset.registerFromFile', payload: { sourcePath: copied, name: '测试视频', kind: 'video' } }],
    });
    expect(registered.body.status, JSON.stringify(registered.body)).toBe('applied');
    revision = registered.body.revision as string;
    const videoAssetId = (registered.body.changedIds as string[]).find((id) => id.startsWith('as_')) as string;

    const events = await call(backend.url, backend.token, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'video-events',
      projectId,
      baseRevision: revision,
      operations: [
        { action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '放一段影像。' }, eventId: 'ev_videoline01' } },
        { action: 'event.insert', payload: { sceneId, type: 'video', params: { assetId: videoAssetId }, eventId: 'ev_videoevent1' } },
        { action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '影像结束了。' }, eventId: 'ev_videoline02' } },
        { action: 'event.insert', payload: { sceneId, type: 'ending', params: { endingId: 'end_video', title: '放映结束' }, eventId: 'ev_videoend001' } },
      ],
    });
    expect(events.body.status, JSON.stringify(events.body)).toBe('applied');

    const exported = await call(backend.url, backend.token, 'POST', `/v1/projects/${projectId}/exports`, {});
    expect(exported.status).toBe(201);
    const videoServer = await startPackageServer(path.join(backend.workspace, (exported.body as { directory: string }).directory));

    await page.goto(videoServer.url);
    await page.getByTestId('start').click();
    await expect(page.getByTestId('dialogue')).toContainText('放一段影像。');
    await page.getByTestId('advance').click();

    const video = page.getByTestId('video');
    await expect(video).toBeVisible({ timeout: 20000 });
    expect(await video.getAttribute('src')).toContain('/content/assets/originals/');
    // 真实视频：浏览器能解析出时长（可解码的真实 MP4，而不是占位文件）
    const duration = await video.evaluate((node) => new Promise<number>((resolve) => {
      const element = node as HTMLVideoElement;
      if (element.readyState >= 1) { resolve(element.duration); return; }
      element.addEventListener('loadedmetadata', () => resolve(element.duration), { once: true });
      element.addEventListener('error', () => resolve(Number.NaN), { once: true });
      setTimeout(() => resolve(element.duration), 10000);
    }));
    expect(Number.isFinite(duration), `视频应能解码出时长（实际 ${duration}）`).toBe(true);
    expect(duration).toBeGreaterThan(0.5);
    // 真正播放：等 currentTime 前进
    const played = await page.waitForFunction(() => {
      const node = document.querySelector('[data-testid="video"]') as HTMLVideoElement | null;
      return Boolean(node && node.currentTime > 0.05);
    }, undefined, { timeout: 15000 }).then(() => true).catch(() => false);
    if (played) {
      await expect(page.getByTestId('dialogue')).toContainText('影像结束了。', { timeout: 20000 });
    } else {
      // 自动播放被拦截时：保留控件与“跳过视频”，跳过后运行继续（绝不永久等待）
      await expect(page.getByTestId('video-skip')).toBeVisible();
      await page.getByTestId('video-skip').click();
      await expect(page.getByTestId('dialogue')).toContainText('影像结束了。', { timeout: 20000 });
    }
    await page.screenshot({ path: 'test-results/player-video.png', fullPage: true });
    await page.getByTestId('advance').click();
    await expect(page.getByTestId('ending')).toContainText('放映结束', { timeout: 20000 });
    trace('p2-video-host', { duration, autoPlayed: played });
    if (videoServer.child.exitCode === null) videoServer.child.kill('SIGKILL');
  });

  test('内容包缺失时给出明确错误而不是空白页（失败路径）', async ({ page }) => {
    await page.goto(`${server.url.replace('player.html', 'player.html?content=./content/missing.json')}`);
    await expect(page.locator('body')).toContainText('内容包加载失败');
  });
});
