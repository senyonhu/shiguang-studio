// This suite begins with an empty service. All content creation and editing goes through the UI.
// Its API credential has read scope only and is used to verify the resulting persisted content.
import { expect, test as base } from '@playwright/test';
import type { Page } from '@playwright/test';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { encodeSolidPng } from '../../server/src/media/decode.ts';
import { EVENT_TYPES } from '../../src/runtime/events.ts';
import type { EventType } from '../../src/runtime/events.ts';
import type { Outline, ProjectExport, SceneDetail } from '../../src/studio/live/api.ts';
import { LEVEL_HTML } from '../studio/helpers/story.ts';

const REPO_ROOT = path.resolve(fileURLToPath(new URL('../..', import.meta.url)));

interface Backend { url: string; token: string; workspace: string; child: ChildProcess }

async function stopBackend(backend: Backend): Promise<void> {
  if (backend.child.exitCode === null && backend.child.signalCode === null) {
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, 3000);
      backend.child.once('close', () => { clearTimeout(timer); resolve(); });
      backend.child.kill('SIGKILL');
    });
  }
  // The only recursive deletion target is the verified temporary directory made by this test.
  const temporaryRoot = path.resolve(os.tmpdir());
  const target = path.resolve(backend.workspace);
  if (path.dirname(target) !== temporaryRoot || !path.basename(target).startsWith('studio-e2e-human-')) throw new Error('Unexpected test cleanup directory');
  rmSync(target, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}

async function startBackend(): Promise<Backend> {
  const workspace = mkdtempSync(path.join(os.tmpdir(), 'studio-e2e-human-'));
  const child = spawn(process.execPath, [path.join(REPO_ROOT, 'server', 'src', 'main.ts'), '--workspace', workspace, '--port', '0'], { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  child.stdout?.resume();
  child.stderr?.resume();
  const backend: Backend = { url: '', token: '', workspace, child };
  try {
    for (let attempt = 0; attempt < 100 && !backend.url; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      try {
        const discovery = JSON.parse(readFileSync(path.join(workspace, '.studio-service', 'runtime.json'), 'utf8')) as { url: string };
        const response = await fetch(`${discovery.url}/v1/health`);
        if (response.ok && ((await response.json()) as { status: string }).status === 'ok') backend.url = discovery.url;
      } catch { /* Wait for the independent service to become ready. */ }
    }
    if (!backend.url) throw new Error('Independent empty backend did not start');
    const pairing = JSON.parse(readFileSync(path.join(workspace, '.studio-service', 'credentials', 'pairing.key'), 'utf8')) as { pairingKey: string };
    const response = await fetch(`${backend.url}/v1/sessions`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ pairingKey: pairing.pairingKey, clientName: 'human-author-read-verification', scopes: ['read'] }),
    });
    if (!response.ok) throw new Error('Could not create read-only verification session');
    backend.token = ((await response.json()) as { token: string }).token;
    return backend;
  } catch (error) {
    await stopBackend(backend);
    throw error;
  }
}

const test = base.extend<{ backend: Backend }>({
  backend: async ({}, use) => {
    const backend = await startBackend();
    try { await use(backend); } finally { await stopBackend(backend); }
  },
});

async function readApi<T>(backend: Backend, endpoint: string): Promise<T> {
  const response = await fetch(`${backend.url}${endpoint}`, { headers: { authorization: `Bearer ${backend.token}` } });
  expect(response.ok, `Read-only verification: ${endpoint}`).toBe(true);
  return await response.json() as T;
}

async function createWork(page: Page, backend: Backend, title: string): Promise<{ projectId: string; sceneId: string }> {
  await page.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
  await expect(page.getByTestId('connection')).toContainText('后台已连接');
  await expect(page.getByTestId('welcome')).toBeVisible();
  await expect(page.getByTestId('export-project')).toBeDisabled();
  await expect(page.getByTestId('preview-project')).toBeDisabled();
  await expect(page.getByTestId('validation')).not.toBeVisible();
  await expect(page.getByTestId('variable-create')).not.toBeVisible();
  await expect(page.getByTestId('asset-import')).not.toBeVisible();
  await page.screenshot({ path: 'test-results/studio-human-welcome.png', fullPage: true });
  await page.getByTestId('new-project').click();
  await page.getByTestId('structure-name').fill(title);
  await page.getByTestId('structure-save').click();
  await expect(page.getByTestId('welcome')).not.toBeVisible();
  await expect(page.getByTestId('outline')).toContainText('第一章');
  await expect(page.getByTestId('outline')).toContainText('开场');
  const projectId = await page.getByTestId('project-select').inputValue();
  const outline = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
  expect(outline.outline).toHaveLength(1);
  expect(outline.outline[0].scenes).toHaveLength(1);
  return { projectId, sceneId: outline.outline[0].scenes[0].sceneId };
}

async function addEvent(page: Page, type: EventType, configure?: (page: Page) => Promise<void>): Promise<void> {
  const count = await page.getByTestId('event-list').locator('li').count();
  await page.getByTestId('insert-type').selectOption(type);
  await page.getByTestId('insert-event').click();
  await expect(page.getByTestId('submit-draft')).toBeVisible();
  if (configure) await configure(page);
  await page.getByTestId('submit-draft').click();
  await expect(page.getByTestId('cancel-draft')).not.toBeVisible();
  await expect(page.getByTestId('event-list').locator('li')).toHaveCount(count + 1);
  await expect(page.getByTestId('message')).toContainText('已插入事件');
}

async function addDialogue(page: Page, text: string): Promise<void> {
  await addEvent(page, 'dialogue', async (editor) => { await editor.getByTestId('param-text').fill(text); });
}

async function createScene(page: Page, name: string): Promise<void> {
  await page.getByTestId('new-scene').click();
  await page.getByTestId('structure-name').fill(name);
  await page.getByTestId('structure-save').click();
  await expect(page.getByTestId('outline')).toContainText(name);
  await expect(page.getByTestId('insert-event')).toBeEnabled();
}

function silentWav(): Buffer {
  const sampleRate = 8000;
  const data = Buffer.alloc(sampleRate / 5 * 2);
  const header = Buffer.alloc(44);
  header.write('RIFF', 0, 'ascii'); header.writeUInt32LE(36 + data.length, 4); header.write('WAVE', 8, 'ascii');
  header.write('fmt ', 12, 'ascii'); header.writeUInt32LE(16, 16); header.writeUInt16LE(1, 20); header.writeUInt16LE(1, 22);
  header.writeUInt32LE(sampleRate, 24); header.writeUInt32LE(sampleRate * 2, 28); header.writeUInt16LE(2, 32); header.writeUInt16LE(16, 34);
  header.write('data', 36, 'ascii'); header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

async function importAsset(page: Page, kind: string, name: string, file: { name: string; mimeType: string; buffer: Buffer }): Promise<void> {
  await page.getByTestId('asset-kind').selectOption(kind);
  await page.getByTestId('asset-file').setInputFiles(file);
  await page.getByTestId('asset-name').fill(name);
  await page.getByTestId('asset-import').click();
  await expect(page.getByTestId('asset-list')).toContainText(name);
  await expect(page.getByTestId('message')).toContainText('素材已导入');
  await expect(page.getByTestId('asset-import')).toBeEnabled();
}

test('空工作区作者通过界面创建作品、导入自己的素材并配置全部 11 种事件', async ({ page, backend }) => {
  const { projectId, sceneId } = await createWork(page, backend, '人工从零制作');

  await importAsset(page, 'background', '雪地背景', { name: 'snow.png', mimeType: 'image/png', buffer: encodeSolidPng(32, 24, [210, 220, 240]) });
  await importAsset(page, 'character', '守塔人立绘', { name: 'keeper.png', mimeType: 'image/png', buffer: encodeSolidPng(16, 32, [180, 100, 70]) });
  await importAsset(page, 'audio', '门铃音效', { name: 'bell.wav', mimeType: 'audio/wav', buffer: silentWav() });
  await importAsset(page, 'video', '雪夜影像', { name: 'snow.mp4', mimeType: 'video/mp4', buffer: readFileSync(path.join(REPO_ROOT, 'tests', 'studio', 'fixtures', 'sample-1s.mp4')) });

  await page.getByTestId('tab-variables').click();
  await page.getByTestId('variable-new-name').fill('灯芯数量');
  await page.getByTestId('variable-new-type').selectOption('number');
  await page.getByTestId('variable-new-initial').fill('3');
  await page.getByTestId('variable-create').click();
  await expect(page.getByTestId('variable-list')).toContainText('灯芯数量');
  await page.getByTestId('tab-characters').click();
  await page.getByTestId('character-new-name').fill('守塔人');
  await page.getByTestId('character-create').click();
  await expect(page.getByTestId('character-list').locator('input')).toHaveValue('守塔人');
  await page.getByTestId('tab-levels').click();
  await page.getByTestId('level-new-title').fill('回声试炼');
  await page.getByTestId('level-file').setInputFiles({ name: 'echo.html', mimeType: 'text/html', buffer: Buffer.from(LEVEL_HTML) });
  await page.getByTestId('level-install').click();
  await expect(page.getByTestId('level-list')).toContainText('回声试炼');
  await expect(page.getByTestId('message')).toContainText('关卡程序已安装');

  await createScene(page, '岔路');
  await page.getByTestId(`scene-${sceneId}`).click();
  await expect(page.getByTestId('event-list').locator('li')).toHaveCount(0);
  await addEvent(page, 'background', async (editor) => { await editor.getByTestId('param-assetId').selectOption({ label: '雪地背景（背景）' }); });
  await addEvent(page, 'character', async (editor) => { await editor.getByTestId('param-assetId').selectOption({ label: '守塔人立绘（立绘）' }); });
  await addEvent(page, 'dialogue', async (editor) => {
    await editor.getByTestId('param-speaker-character').selectOption({ label: '守塔人' });
    await expect(editor.getByTestId('param-speaker')).toHaveValue('守塔人');
    await editor.getByTestId('param-text').fill('123');
  });
  await addEvent(page, 'choice', async (editor) => {
    await expect(editor.getByTestId('param-options').locator('textarea')).toHaveCount(0);
    await editor.getByTestId('choice-label-0').fill('沿着雪线前行');
    await editor.getByTestId('choice-scene-0').selectOption({ label: '第一章 / 岔路' });
    await editor.getByTestId('choice-add').click();
    await editor.getByTestId('choice-label-1').fill('回到开场');
    await editor.getByTestId('choice-scene-1').selectOption({ label: '第一章 / 开场' });
  });
  await addEvent(page, 'variable', async (editor) => {
    await editor.getByTestId('param-variableId').selectOption({ label: '灯芯数量（数值）' });
    await editor.getByTestId('param-op').selectOption({ label: '增加' });
    await editor.getByTestId('param-value').fill('2');
  });
  await addEvent(page, 'audio', async (editor) => { await editor.getByTestId('param-assetId').selectOption({ label: '门铃音效（音频）' }); });
  await addEvent(page, 'video', async (editor) => { await editor.getByTestId('param-assetId').selectOption({ label: '雪夜影像（视频）' }); });
  await addEvent(page, 'wait', async (editor) => { await editor.getByTestId('param-ms').fill('120'); });
  await addEvent(page, 'level', async (editor) => { await editor.getByTestId('param-levelId').selectOption({ label: '回声试炼' }); });
  await addEvent(page, 'jump', async (editor) => { await editor.getByTestId('param-targetSceneId').selectOption({ label: '第一章 / 岔路' }); });
  await addEvent(page, 'ending', async (editor) => {
    await editor.getByTestId('param-endingId').fill('重逢');
    await editor.getByTestId('param-title').fill('雪线尽头');
  });

  const storedScene = await readApi<SceneDetail>(backend, `/v1/projects/${projectId}/scenes/${sceneId}`);
  expect(storedScene.events.map((event) => event.type)).toEqual(EVENT_TYPES);
  expect(storedScene.events.find((event) => event.type === 'dialogue')?.params.text).toBe('123');
  expect(storedScene.events.find((event) => event.type === 'choice')?.params.options).toHaveLength(2);
  const validation = await readApi<{ ok: boolean; issues: unknown[] }>(backend, `/v1/projects/${projectId}/validate`);
  expect(validation).toMatchObject({ ok: true, issues: [] });
  const outline = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
  expect(readFileSync(path.join(backend.workspace, projectId, outline.levels[0].entry), 'utf8')).toContain('已收到宿主初始化');
  await page.screenshot({ path: 'test-results/studio-human-workspace.png', fullPage: true });
});

test('未保存草稿阻止切换事件和场景，另一位作者提交后冲突也保留输入', async ({ page, backend, context }) => {
  const { projectId, sceneId } = await createWork(page, backend, '草稿保护');
  await addDialogue(page, '第一句');
  await addDialogue(page, '第二句');
  await createScene(page, '下一场景');
  await page.getByTestId(`scene-${sceneId}`).click();
  await page.getByTestId('event-list').getByRole('button', { name: /第一句/ }).click();
  await page.getByTestId('start-edit').click();
  await page.getByTestId('param-text').fill('尚未保存的123');
  await page.getByTestId('event-list').getByRole('button', { name: /第二句/ }).click();
  await expect(page.getByTestId('param-text')).toHaveValue('尚未保存的123');
  await expect(page.getByTestId('draft-notice')).toContainText('草稿');
  const structure = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
  const otherScene = structure.outline[0].scenes.find((scene) => scene.title === '下一场景')!;
  await page.getByTestId(`scene-${otherScene.sceneId}`).click();
  await expect(page.getByTestId(`scene-${sceneId}`)).toHaveClass(/active/);
  await expect(page.getByTestId('param-text')).toHaveValue('尚未保存的123');
  await expect(page.getByTestId('export-project')).toBeDisabled();

  const otherAuthor = await context.newPage();
  await otherAuthor.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
  await expect(otherAuthor.getByTestId('connection')).toContainText('后台已连接');
  await expect(otherAuthor.getByTestId('event-list').locator('li')).toHaveCount(2);
  await addDialogue(otherAuthor, '另一位作者的新对白');
  await expect(page.getByTestId('draft-notice')).toContainText('草稿', { timeout: 20000 });
  await page.getByTestId('submit-draft').click();
  await expect(page.getByTestId('message')).toContainText('提交未生效');
  await expect(page.getByTestId('param-text')).toHaveValue('尚未保存的123');
  const stored = await readApi<SceneDetail>(backend, `/v1/projects/${projectId}/scenes/${sceneId}`);
  expect(stored.events.find((event) => event.params.text === '第一句')).toBeDefined();
  expect(stored.events.some((event) => event.params.text === '尚未保存的123')).toBe(false);
  await page.getByTestId('cancel-draft').click();
  await otherAuthor.close();
});

test('提交的摘要计算尚未完成时输入和取消立即锁定，保存不会覆盖后续草稿', async ({ page, backend }) => {
  await page.addInitScript(() => {
    const originalDigest = crypto.subtle.digest.bind(crypto.subtle);
    const testWindow = window as typeof window & { __digestStarted?: boolean; __finishDigest?: () => void };
    Object.defineProperty(crypto.subtle, 'digest', {
      configurable: true,
      value: async (algorithm: AlgorithmIdentifier, data: BufferSource) => {
        testWindow.__digestStarted = true;
        await new Promise<void>((resolve) => { testWindow.__finishDigest = resolve; });
        return originalDigest(algorithm, data);
      },
    });
  });
  const { projectId, sceneId } = await createWork(page, backend, '提交期间保护');
  await page.getByTestId('insert-type').selectOption('dialogue');
  await page.getByTestId('insert-event').click();
  await page.getByTestId('param-text').fill('等待摘要计算的对白');
  await page.getByTestId('submit-draft').click();
  await expect.poll(() => page.evaluate(() => (window as typeof window & { __digestStarted?: boolean }).__digestStarted)).toBe(true);
  await expect(page.getByTestId('param-text')).toBeDisabled();
  await expect(page.getByTestId('cancel-draft')).toBeDisabled();
  await expect(page.getByTestId('submit-draft')).toBeDisabled();
  await expect(page.getByTestId('insert-event')).toBeDisabled();
  // Disabled native buttons ignore clicks; the submitting draft must remain the same.
  await page.getByTestId('cancel-draft').click({ force: true });
  await page.getByTestId('insert-event').click({ force: true });
  await expect(page.getByTestId('param-text')).toHaveValue('等待摘要计算的对白');
  expect((await readApi<SceneDetail>(backend, `/v1/projects/${projectId}/scenes/${sceneId}`)).events).toHaveLength(0);
  await page.evaluate(() => (window as typeof window & { __finishDigest?: () => void }).__finishDigest?.());
  await expect(page.getByTestId('cancel-draft')).not.toBeVisible();
  await expect(page.getByTestId('event-list')).toContainText('等待摘要计算的对白');
  const saved = await readApi<SceneDetail>(backend, `/v1/projects/${projectId}/scenes/${sceneId}`);
  expect(saved.events).toHaveLength(1);
  expect(saved.events[0].params.text).toBe('等待摘要计算的对白');
  await page.getByTestId('insert-event').click();
  await page.getByTestId('param-text').fill('保存完成之后的新草稿');
  await expect(page.getByTestId('param-text')).toHaveValue('保存完成之后的新草稿');
  await expect(page.getByTestId('revision')).toContainText('有未保存草稿');
  await page.getByTestId('cancel-draft').click();
});

test('未保存主题在另一作者提交时保留，旧版本保存被拒绝后可取消并重新保存', async ({ page, backend, context }) => {
  const { projectId } = await createWork(page, backend, '主题协作保护');
  await page.getByTestId('tab-theme').click();
  const originalColor = await page.getByTestId('theme-text-color').inputValue();
  await page.getByTestId('theme-text-color').fill('#ff8844');
  await expect(page.getByTestId('revision')).toContainText('有未保存草稿');
  await expect(page.getByTestId('export-project')).toBeDisabled();
  await expect(page.getByTestId('preview-project')).toBeDisabled();

  const otherAuthor = await context.newPage();
  await otherAuthor.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
  await expect(otherAuthor.getByTestId('connection')).toContainText('后台已连接');
  await expect(otherAuthor.getByTestId('validation')).toContainText('结构校验通过');
  await addDialogue(otherAuthor, '另一作者推动了作品修订');
  await expect(page.getByTestId('revision')).toContainText('修订 1');
  await expect(page.getByTestId('theme-text-color')).toHaveValue('#ff8844');
  await expect(page.getByTestId('revision')).toContainText('有未保存草稿');
  await expect(page.getByTestId('export-project')).toBeDisabled();

  await page.getByTestId('theme-save').click();
  await expect(page.getByTestId('message')).toContainText('修改主题被拒绝');
  await expect(page.getByTestId('theme-text-color')).toHaveValue('#ff8844');
  await expect(page.getByTestId('theme-cancel')).toBeVisible();
  let stored = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
  expect(stored.theme?.dialogue.textColor).toBe(originalColor);
  await page.getByTestId('theme-cancel').click();
  await expect(page.getByTestId('theme-text-color')).toHaveValue(originalColor);
  await expect(page.getByTestId('revision')).toContainText('修改已保存');
  await page.getByTestId('theme-text-color').fill('#6688ff');
  await page.getByTestId('theme-save').click();
  await expect(page.getByTestId('message')).toContainText('修改主题完成');
  await expect(page.getByTestId('theme-cancel')).not.toBeVisible();
  await expect(page.getByTestId('export-project')).toBeEnabled();
  stored = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
  expect(stored.theme?.dialogue.textColor).toBe('#6688ff');
  await otherAuthor.close();
});

test('作者可在中间插入并排序事件，试玩和导出验证会打开真实展示器', async ({ page, backend }) => {
  const { projectId, sceneId } = await createWork(page, backend, '可试玩的人工作品');
  await addDialogue(page, '第一句：这是人工创作的作品。');
  await addDialogue(page, '第二句：雪线尽头。');
  await page.getByTestId('event-list').getByRole('button', { name: /第一句/ }).click();
  await addEvent(page, 'wait', async (editor) => { await editor.getByTestId('param-ms').fill('10'); });
  let stored = await readApi<SceneDetail>(backend, `/v1/projects/${projectId}/scenes/${sceneId}`);
  expect(stored.events.map((event) => event.type)).toEqual(['dialogue', 'wait', 'dialogue']);
  const movedEventId = stored.events[1].eventId;
  await page.getByTestId('event-up').click();
  await expect.poll(async () => (await readApi<SceneDetail>(backend, `/v1/projects/${projectId}/scenes/${sceneId}`)).events[0].eventId).toBe(movedEventId);
  await page.getByTestId('event-down').click();
  await expect.poll(async () => (await readApi<SceneDetail>(backend, `/v1/projects/${projectId}/scenes/${sceneId}`)).events[1].eventId).toBe(movedEventId);

  await page.getByTestId('preview-scene').click();
  await expect(page.getByTestId('playback-link')).toBeVisible();
  const [playback] = await Promise.all([page.waitForEvent('popup'), page.getByTestId('playback-link').click()]);
  await expect(playback.getByTestId('start')).toBeEnabled();
  await playback.getByTestId('start').click();
  await expect(playback.getByTestId('dialogue')).toContainText('第一句：这是人工创作的作品。');
  await playback.getByTestId('advance').click();
  await expect(playback.getByTestId('dialogue')).toContainText('第二句：雪线尽头。');
  await playback.close();

  const exportResponse = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === `/v1/projects/${projectId}/exports`);
  await page.getByTestId('export-project').click();
  const response = await exportResponse;
  expect(response.status()).toBe(201);
  const exported = await response.json() as ProjectExport;
  expect(exported.playerIncluded).toBe(true);
  expect(exported.missingAssets).toEqual([]);
  expect(exported.sensitiveHits).toEqual([]);
  await expect(page.getByTestId('export-result')).toBeVisible();
  if (exported.portableIncluded) await expect(page.getByTestId('export-result')).toContainText('已包含展示器与运行环境');
  else {
    await expect(page.getByTestId('export-result')).toContainText('需要 Node');
    await expect(page.getByTestId('export-result')).not.toContainText('无需安装');
  }
  await page.getByTestId('preview-export').click();
  await expect(page.getByTestId('export-playback-link')).toBeVisible();
  const [published] = await Promise.all([page.waitForEvent('popup'), page.getByTestId('export-playback-link').click()]);
  await expect(published.getByTestId('start')).toBeEnabled();
  await published.getByTestId('start').click();
  await expect(published.getByTestId('dialogue')).toContainText('第一句：这是人工创作的作品。');
  await published.close();
  stored = await readApi<SceneDetail>(backend, `/v1/projects/${projectId}/scenes/${sceneId}`);
  expect(stored.events.map((event) => event.type)).toEqual(['dialogue', 'wait', 'dialogue']);
  await page.screenshot({ path: 'test-results/studio-human-playback-export.png', fullPage: true });
});
