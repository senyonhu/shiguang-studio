// Independent audit regressions: every test creates an empty temporary author workspace.
// Network failures and delayed subscriptions affect only the isolated browser page.
import { expect, test as base } from '@playwright/test';
import type { Page } from '@playwright/test';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import type { EditOutcome, Outline, SceneDetail } from '../../src/studio/live/api.ts';

const REPO_ROOT = path.resolve(fileURLToPath(new URL('../..', import.meta.url)));
interface Backend { url: string; readToken: string; editToken: string; workspace: string; child: ChildProcess }

async function stopBackend(backend: Backend): Promise<void> {
  if (backend.child.exitCode === null && backend.child.signalCode === null) {
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, 3000);
      backend.child.once('close', () => { clearTimeout(timer); resolve(); });
      backend.child.kill('SIGKILL');
    });
  }
  // Only delete the exact direct child of the OS temp directory created by this fixture.
  const temporaryRoot = path.resolve(os.tmpdir());
  const target = path.resolve(backend.workspace);
  if (path.dirname(target) !== temporaryRoot || !path.basename(target).startsWith('studio-e2e-audit-')) throw new Error('Unexpected audit cleanup directory');
  rmSync(target, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}

async function startBackend(): Promise<Backend> {
  const workspace = mkdtempSync(path.join(os.tmpdir(), 'studio-e2e-audit-'));
  const child = spawn(process.execPath, [path.join(REPO_ROOT, 'server', 'src', 'main.ts'), '--workspace', workspace, '--port', '0'], { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  child.stdout?.resume();
  child.stderr?.resume();
  const backend: Backend = { url: '', readToken: '', editToken: '', workspace, child };
  try {
    for (let attempt = 0; attempt < 100 && !backend.url; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 100));
      try {
        const discovery = JSON.parse(readFileSync(path.join(workspace, '.studio-service', 'runtime.json'), 'utf8')) as { url: string };
        const response = await fetch(`${discovery.url}/v1/health`);
        if (response.ok && ((await response.json()) as { status: string }).status === 'ok') backend.url = discovery.url;
      } catch { /* Wait for the independent service to become ready. */ }
    }
    if (!backend.url) throw new Error('Independent audit backend did not start');
    const pairing = JSON.parse(readFileSync(path.join(workspace, '.studio-service', 'credentials', 'pairing.key'), 'utf8')) as { pairingKey: string };
    const response = await fetch(`${backend.url}/v1/sessions`, {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ pairingKey: pairing.pairingKey, clientName: 'audit-read-verification', scopes: ['read'] }),
    });
    if (!response.ok) throw new Error('Could not establish audit read session');
    backend.readToken = ((await response.json()) as { token: string }).token;
    // A second human author creates the concurrent commits through the same HTTP API.
    const human = await fetch(`${backend.url}/v1/ui-session`, { method: 'POST', headers: { origin: 'http://127.0.0.1:5173' } });
    if (!human.ok) throw new Error('Could not establish independent human author session');
    backend.editToken = ((await human.json()) as { token: string }).token;
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
  const response = await fetch(`${backend.url}${endpoint}`, { headers: { authorization: `Bearer ${backend.readToken}` } });
  expect(response.ok, `Read-only audit verification: ${endpoint}`).toBe(true);
  return await response.json() as T;
}

async function externalEdit(backend: Backend, projectId: string, operations: { action: string; payload: Record<string, unknown> }[]): Promise<EditOutcome> {
  const outline = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
  const response = await fetch(`${backend.url}/v1/projects/${projectId}/transactions`, {
    method: 'POST', headers: { authorization: `Bearer ${backend.editToken}`, 'content-type': 'application/json' },
    body: JSON.stringify({ requestId: `audit-concurrent-${crypto.randomUUID()}`, baseRevision: outline.revision, operations }),
  });
  expect(response.ok).toBe(true);
  const outcome = await response.json() as EditOutcome;
  expect(outcome.status).toBe('applied');
  return outcome;
}

async function createWork(page: Page, backend: Backend): Promise<{ projectId: string; sceneId: string }> {
  // Keep this isolated workspace authoritative even if a local portable service is discoverable.
  await page.route('**/__studio/discovery', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }));
  await page.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
  await expect(page.getByTestId('welcome')).toBeVisible();
  await page.getByTestId('new-project').click();
  await page.getByTestId('structure-name').fill('审计回归作品');
  await page.getByTestId('structure-save').click();
  await expect(page.getByTestId('structure-save')).not.toBeVisible();
  await expect(page.getByTestId('insert-event')).toBeEnabled();
  const projectId = await page.getByTestId('project-select').inputValue();
  const outline = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
  return { projectId, sceneId: outline.outline[0].scenes[0].sceneId };
}

async function addDialogue(page: Page, text: string): Promise<void> {
  await page.getByTestId('insert-type').selectOption('dialogue');
  await page.getByTestId('insert-event').click();
  await page.getByTestId('param-text').fill(text);
  await page.getByTestId('submit-draft').click();
  await expect(page.getByTestId('cancel-draft')).not.toBeVisible();
  await expect(page.getByTestId('event-list')).toContainText(text);
  await expect(page.getByTestId('start-edit')).toBeEnabled();
}

test('主题未保存时不能开始事件草稿', async ({ page, backend }) => {
  const { projectId } = await createWork(page, backend);
  await addDialogue(page, '已保存的对白');
  await page.getByTestId('tab-theme').click();
  await page.getByTestId('theme-font-size').fill('24');
  await expect(page.getByTestId('revision')).toContainText('有未保存草稿');
  await expect(page.getByTestId('start-edit')).toBeDisabled();
  await expect(page.getByTestId('submit-draft')).not.toBeVisible();
  await page.getByTestId('theme-save').click();
  await expect(page.getByTestId('revision')).toContainText('修改已保存');
  await expect(page.getByTestId('start-edit')).toBeEnabled();
  const persisted = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
  expect(persisted.theme?.dialogue.fontSize).toBe(24);
});

test('取消草稿会读取期间外部作者的新修订', async ({ page, backend }) => {
  const { projectId, sceneId } = await createWork(page, backend);
  await addDialogue(page, '旧对白');
  const scene = await readApi<SceneDetail>(backend, `/v1/projects/${projectId}/scenes/${sceneId}`);
  await page.getByTestId('start-edit').click();
  await page.getByTestId('param-text').fill('未提交的人工草稿');
  const outcome = await externalEdit(backend, projectId, [{ action: 'event.update', payload: { sceneId, eventId: scene.events[0].eventId, params: { text: '另一作者的新对白' } } }]);
  await expect(page.getByTestId('draft-notice')).toContainText(`新修订 ${outcome.revision}`);
  await expect(page.getByTestId('param-text')).toHaveValue('未提交的人工草稿');
  await page.getByTestId('cancel-draft').click();
  await expect(page.getByTestId('event-list')).toContainText('另一作者的新对白');
  await expect(page.getByTestId('revision')).toContainText(`修订 ${outcome.revision}`);
  await expect(page.getByTestId('validation')).toContainText('结构校验通过');
});

for (const kind of ['chapter', 'scene'] as const) {
  test(`${kind === 'chapter' ? '章节' : '场景'}创建响应未知后重试只创建一次`, async ({ page, backend }) => {
    const { projectId } = await createWork(page, backend);
    await page.getByTestId(kind === 'chapter' ? 'new-chapter' : 'new-scene').click();
    const title = `${kind === 'chapter' ? '章节' : '场景'}只能创建一次`;
    await page.getByTestId('structure-name').fill(title);
    let swallowed = false;
    let responseSwallowed: (() => void) | undefined;
    const swallowedSignal = new Promise<void>((resolve) => { responseSwallowed = resolve; });
    await page.route('**/transactions', async (route) => {
      if (!swallowed && route.request().method() === 'POST') {
        swallowed = true;
        await route.fetch(); // Commit on the real service, then lose only the client response.
        await route.abort('failed');
        responseSwallowed?.();
      } else await route.continue();
    });
    await page.getByTestId('structure-save').click();
    await swallowedSignal;
    await expect.poll(async () => {
      const save = page.getByTestId('structure-save');
      return await save.isVisible() ? (await save.isEnabled() ? 'retry' : 'waiting') : 'recovered';
    }).not.toBe('waiting');
    // Recovery may automatically resolve the committed result, or preserve a safe retry.
    if (await page.getByTestId('structure-save').isVisible()) await page.getByTestId('structure-save').click();
    await expect(page.getByTestId('structure-save')).not.toBeVisible();
    const persisted = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
    const titles = kind === 'chapter' ? persisted.outline.map((chapter) => chapter.title) : persisted.outline.flatMap((chapter) => chapter.scenes.map((scene) => scene.title));
    expect(titles.filter((item) => item === title)).toHaveLength(1);
    expect(titles).toHaveLength(2);
    await expect(page.getByTestId('outline')).toContainText(title);
  });
}

test('SSE 握手迟于初始读取时补读遗漏提交', async ({ page, backend }) => {
  const { projectId, sceneId } = await createWork(page, backend);
  let releaseSubscription: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => { releaseSubscription = resolve; });
  let subscriptionRequested: (() => void) | undefined;
  const seen = new Promise<void>((resolve) => { subscriptionRequested = resolve; });
  await page.route('**/events', async (route) => {
    subscriptionRequested?.();
    await gate;
    await route.continue();
  });
  try {
    await page.reload();
    await seen;
    const outcome = await externalEdit(backend, projectId, [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '握手完成前保存的对白' } } }]);
    releaseSubscription?.();
    await expect(page.getByTestId('event-list')).toContainText('握手完成前保存的对白');
    await expect(page.getByTestId('revision')).toContainText(`修订 ${outcome.revision}`);
    await expect(page.getByTestId('connection')).toContainText('后台已连接');
  } finally { releaseSubscription?.(); }
});

test('清空角色名或模板提示词后恢复并说明未保存', async ({ page, backend }) => {
  const { projectId } = await createWork(page, backend);
  await page.getByTestId('tab-characters').click();
  await page.getByTestId('character-new-name').fill('已保存角色');
  await page.getByTestId('character-create').click();
  await expect(page.getByTestId('character-list').locator('input')).toHaveValue('已保存角色');
  let persisted = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
  const character = page.getByTestId(`character-name-${persisted.characters[0].characterId}`);
  await character.fill('');
  await character.press('Tab');
  await expect(character).toHaveValue('已保存角色');
  await expect(page.getByTestId('message')).toContainText(/不能为空|未保存|已恢复|请填写/);

  await page.getByTestId('tab-recipes').click();
  await page.getByTestId('recipe-new-name').fill('已保存模板');
  await page.getByTestId('recipe-new-prompt').fill('已保存的提示词');
  await page.getByTestId('recipe-create').click();
  await expect(page.getByTestId('recipe-list')).toContainText('已保存模板');
  persisted = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
  const recipe = page.getByTestId(`recipe-prompt-${persisted.recipes![0].recipeId}`);
  await recipe.fill('');
  await recipe.press('Tab');
  await expect(recipe).toHaveValue('已保存的提示词');
  await expect(page.getByTestId('message')).toContainText(/不能为空|未保存|已恢复|请填写/);
  persisted = await readApi<Outline>(backend, `/v1/projects/${projectId}/outline`);
  expect(persisted.characters[0].name).toBe('已保存角色');
  expect(persisted.recipes![0].prompt).toBe('已保存的提示词');
});
