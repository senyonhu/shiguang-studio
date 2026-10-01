// 人工编辑器界面（studio-live.html）的浏览器验证：
// 关闭外部 AI 的情况下，后台由 AI/API 制作的内容必须显示在人工界面；人工修改必须能被 API 读到。
// 依赖：vite 开发服务器（127.0.0.1:5173）已运行；本测试自行启动独立后台进程与临时工作区。
import { expect, test } from '@playwright/test';
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { encodeSolidPng } from '../../server/src/media/decode.ts';
import { LEVEL_HTML } from '../studio/helpers/story.ts';

const REPO_ROOT = path.resolve(fileURLToPath(new URL('../..', import.meta.url)));
const SERVER_ENTRY = path.join(REPO_ROOT, 'server', 'src', 'main.ts');

interface Backend {
  url: string;
  token: string;
  workspace: string;
  child: ChildProcess;
  stop: () => Promise<void>;
}

async function startBackend(): Promise<Backend> {
  const workspace = mkdtempSync(path.join(os.tmpdir(), 'studio-e2e-ui-'));
  const child = spawn(process.execPath, [SERVER_ENTRY, '--workspace', workspace, '--port', '0'], { stdio: ['ignore', 'pipe', 'pipe'] });
  const discoveryFile = path.join(workspace, '.studio-service', 'runtime.json');
  let url = '';
  for (let attempt = 0; attempt < 100 && !url; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, 100));
    try {
      const record = JSON.parse(await (await import('node:fs')).promises.readFile(discoveryFile, 'utf8')) as { url: string };
      const health = await fetch(`${record.url}/v1/health`).then((response) => response.json()).catch(() => null) as { status?: string } | null;
      if (health?.status === 'ok') url = record.url;
    } catch { /* 等待后台写出发现记录 */ }
  }
  if (!url) throw new Error('后台未能启动');
  const pairingKey = (JSON.parse(await (await import('node:fs')).promises.readFile(path.join(workspace, '.studio-service', 'credentials', 'pairing.key'), 'utf8')) as { pairingKey: string }).pairingKey;
  const session = await fetch(`${url}/v1/sessions`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ pairingKey, clientName: 'e2e-ai', scopes: ['read', 'edit', 'generate', 'preview', 'export'] }),
  }).then((response) => response.json()) as { token: string };
  return {
    url,
    token: session.token,
    workspace,
    child,
    stop: async () => {
      if (child.exitCode === null) child.kill('SIGKILL');
      await new Promise((resolve) => setTimeout(resolve, 150));
    },
  };
}

async function api(backend: Backend, method: 'GET' | 'POST', pathname: string, body?: unknown): Promise<{ status: number; body: Record<string, unknown> }> {
  const response = await fetch(`${backend.url}${pathname}`, {
    method,
    headers: { authorization: `Bearer ${backend.token}`, 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) as Record<string, unknown> : {} };
}

test.describe('人工编辑器读取后台工程', () => {
  let backend: Backend;
  let projectId = '';
  let sceneId = '';
  let chapterId = '';
  let revision = '';
  let dialogueId = '';

  test.beforeAll(async () => {
    backend = await startBackend();
    const created = await api(backend, 'POST', '/v1/projects', { title: '界面联调工程', chapterTitle: '第一章', sceneTitle: '塔前' });
    projectId = created.body.projectId as string;
    sceneId = created.body.sceneId as string;
    chapterId = created.body.chapterId as string;
    revision = created.body.revision as string;

    // 先导入一张真实 PNG（写入后台已授权的导入目录），再模拟“外部 AI 通过工具制作”的结果
    const importPath = path.join(backend.workspace, 'imports', 'rain-night.png');
    writeFileSync(importPath, encodeSolidPng(8, 6, [24, 32, 48]));
    const imported = await api(backend, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'e2e-asset',
      projectId,
      baseRevision: revision,
      operations: [{ action: 'asset.registerFromFile', payload: { sourcePath: importPath, name: '雨夜背景', kind: 'background' } }],
    });
    expect(imported.body.status, JSON.stringify(imported.body)).toBe('applied');
    const assetId = (imported.body.changedIds as string[]).find((id) => id.startsWith('as_')) as string;
    revision = imported.body.revision as string;

    const dialogue = await api(backend, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'e2e-dialogue',
      projectId,
      baseRevision: revision,
      operations: [
        { action: 'event.insert', payload: { sceneId, type: 'background', params: { assetId }, eventId: 'ev_e2ebg000001' } },
        { action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '这是外部 AI 写入的对白。' }, eventId: 'ev_e2edialog01' } },
      ],
    });
    expect(dialogue.body.status, JSON.stringify(dialogue.body)).toBe('applied');
    revision = dialogue.body.revision as string;
    dialogueId = 'ev_e2edialog01';
    expect((dialogue.body.changedIds as string[])).toContain(dialogueId);

    const chapter = await api(backend, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'e2e-chapter',
      projectId,
      baseRevision: revision,
      operations: [{ action: 'chapter.create', payload: { title: '第二章 塔内' } }],
    });
    expect(chapter.body.status, JSON.stringify(chapter.body)).toBe('applied');
    revision = chapter.body.revision as string;
  });

  test.afterAll(async () => {
    await backend?.stop();
  });

  test('界面显示后台工程、章节树与 AI 写入的对白', async ({ page }) => {
    await page.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
    await expect(page.getByTestId('connection')).toContainText('后台已连接', { timeout: 20000 });
    await expect(page.getByTestId('project-select')).toHaveValue(projectId);
    await expect(page.getByTestId('outline')).toContainText('第一章');
    await expect(page.getByTestId('outline')).toContainText('第二章 塔内');
    await expect(page.getByTestId('event-list')).toContainText('这是外部 AI 写入的对白。');
    await page.screenshot({ path: 'test-results/studio-live-overview.png', fullPage: true });
  });

  test('人工修改通过后台保存，API 能读到同一修订', async ({ page }) => {
    await page.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
    await expect(page.getByTestId('connection')).toContainText('后台已连接', { timeout: 20000 });
    await page.getByTestId(`event-${dialogueId}`).click();
    await page.getByTestId('start-edit').click();
    await page.getByTestId('param-text').fill('人工在编辑器里改写的对白。');
    await page.getByTestId('param-speaker').fill('璃（人工）');
    await page.getByTestId('submit-draft').click();
    await expect(page.getByTestId('message')).toContainText('已保存', { timeout: 15000 });

    const scene = await api(backend, 'GET', `/v1/projects/${projectId}/scenes/${sceneId}`);
    const events = scene.body.events as { eventId: string; params: { text?: string; speaker?: string } }[];
    const dialogue = events.find((event) => event.eventId === dialogueId);
    expect(dialogue?.params.text).toBe('人工在编辑器里改写的对白。');
    expect(dialogue?.params.speaker).toBe('璃（人工）');
    revision = scene.body.revision as string;
  });

  test('未提交草稿在后台变化时保留，并提示版本冲突', async ({ page }) => {
    await page.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
    await expect(page.getByTestId('connection')).toContainText('后台已连接', { timeout: 20000 });
    await page.getByTestId(`event-${dialogueId}`).click();
    await page.getByTestId('start-edit').click();
    await page.getByTestId('param-text').fill('尚未提交的草稿文字。');

    // 模拟外部 AI 在编辑期间提交了修改
    const remote = await api(backend, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: `e2e-remote-${Date.now()}`,
      projectId,
      baseRevision: revision,
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'wait', params: { ms: 250 } } }],
    });
    expect(remote.body.status).toBe('applied');
    revision = remote.body.revision as string;

    await expect(page.getByTestId('draft-notice')).toContainText('草稿仍保留', { timeout: 20000 });
    await expect(page.getByTestId('param-text')).toHaveValue('尚未提交的草稿文字。');
    await page.screenshot({ path: 'test-results/studio-live-draft.png', fullPage: true });

    // 使用过期基础版本提交必须被拒绝，且草稿不丢失
    await page.getByTestId('submit-draft').click();
    await expect(page.getByTestId('message')).toContainText('提交未生效', { timeout: 15000 });
    await expect(page.getByTestId('param-text')).toHaveValue('尚未提交的草稿文字。');
  });

  test('人工通过面板新建变量/角色/关卡，后台立即可见（与 AI 工具同一能力）', async ({ page }) => {
    await page.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
    await expect(page.getByTestId('connection')).toContainText('后台已连接', { timeout: 20000 });

    // 变量面板
    await page.getByTestId('tab-variables').click();
    await page.getByTestId('variable-new-name').fill('灯芯数量');
    await page.getByTestId('variable-new-type').selectOption('number');
    await page.getByTestId('variable-new-initial').fill('3');
    await page.getByTestId('variable-create').click();
    await expect(page.getByTestId('variable-list')).toContainText('灯芯数量', { timeout: 15000 });
    await expect(page.getByTestId('message')).toContainText('新建变量完成');

    // 角色面板（角色名是可编辑输入框，断言真实绑定值）
    await page.getByTestId('tab-characters').click();
    await page.getByTestId('character-new-name').fill('守塔人');
    await page.getByTestId('character-create').click();
    await expect(page.getByTestId('character-list').locator('input').first()).toHaveValue('守塔人', { timeout: 15000 });
    await expect(page.getByTestId('message')).toContainText('新建角色完成');

    // 关卡面板
    await page.getByTestId('tab-levels').click();
    await page.getByTestId('level-new-title').fill('塔顶回声');
    await page.getByTestId('level-file').setInputFiles({ name: 'e2e-echo.html', mimeType: 'text/html', buffer: Buffer.from(LEVEL_HTML) });
    await page.getByTestId('level-install').click();
    await expect(page.getByTestId('level-list')).toContainText('塔顶回声', { timeout: 15000 });

    // 后台（AI 工具使用的同一入口）必须能看到这三项
    const snapshot = await api(backend, 'GET', `/v1/projects/${projectId}`);
    const outline = await api(backend, 'GET', `/v1/projects/${projectId}/outline`);
    expect(snapshot.status).toBe(200);
    const variables = outline.body.variables as { name: string; type: string; initial: unknown }[];
    const characters = outline.body.characters as { name: string }[];
    const levels = outline.body.levels as { title: string; entry: string }[];
    expect(variables.some((item) => item.name === '灯芯数量' && item.type === 'number' && item.initial === 3)).toBe(true);
    expect(characters.some((item) => item.name === '守塔人')).toBe(true);
    const installedLevel = levels.find((item) => item.title === '塔顶回声');
    expect(installedLevel?.entry).toMatch(/^levels\/.+\.html$/);
    expect(readFileSync(path.join(backend.workspace, projectId, installedLevel!.entry), 'utf8')).toContain('已收到宿主初始化');

    // 面板修改走同一事务入口：改初值后后台可见，且可以撤销
    const variableId = (await api(backend, 'GET', `/v1/projects/${projectId}/outline`)).body.variables as { variableId: string; name: string }[];
    const target = variableId.find((item) => item.name === '灯芯数量')!;
    await page.getByTestId('tab-variables').click();
    await page.getByTestId(`variable-initial-${target.variableId}`).fill('7');
    await page.getByTestId(`variable-initial-${target.variableId}`).blur();
    await expect(page.getByTestId('message')).toContainText('修改变量初值完成', { timeout: 15000 });
    const afterEdit = await api(backend, 'GET', `/v1/projects/${projectId}/outline`);
    expect((afterEdit.body.variables as { variableId: string; initial: unknown }[]).find((item) => item.variableId === target.variableId)?.initial).toBe(7);

    // 删除角色（面板操作）后后台也不再返回
    const characterId = (afterEdit.body.characters as { characterId: string; name: string }[]).find((item) => item.name === '守塔人')!.characterId;
    await page.getByTestId('tab-characters').click();
    await page.getByTestId(`character-delete-${characterId}`).click();
    await expect(page.getByTestId('message')).toContainText('删除角色完成', { timeout: 15000 });
    const finalOutline = await api(backend, 'GET', `/v1/projects/${projectId}/outline`);
    expect((finalOutline.body.characters as { characterId: string }[]).some((item) => item.characterId === characterId)).toBe(false);
    await page.screenshot({ path: 'test-results/studio-live-panels.png', fullPage: true });
  });

  test('主题面板：界面改主题 → 后台与磁盘可见 → 导出后展示器生效', async ({ page }) => {
    await page.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
    await expect(page.getByTestId('connection')).toContainText('后台已连接', { timeout: 20000 });

    await page.getByTestId('tab-theme').click();
    await page.getByTestId('theme-text-color').fill('#ffcc00');
    await page.getByTestId('theme-font-size').fill('28');
    await page.getByTestId('theme-align').selectOption('center');
    // 预览区立即反映草稿
    await expect(page.getByTestId('theme-preview')).toHaveCSS('color', 'rgb(255, 204, 0)');
    await page.getByTestId('theme-save').click();
    await expect(page.getByTestId('message')).toContainText('修改主题完成', { timeout: 15000 });

    // 后台与磁盘一致
    const outline = await api(backend, 'GET', `/v1/projects/${projectId}/outline`);
    const theme = (outline.body as { theme?: { dialogue: { textColor: string; fontSize: number; align: string } } }).theme;
    expect(theme?.dialogue.textColor).toBe('#ffcc00');
    expect(theme?.dialogue.fontSize).toBe(28);
    expect(theme?.dialogue.align).toBe('center');
    const themeFile = JSON.parse(readFileSync(path.join(backend.workspace, projectId, 'themes', 'theme.json'), 'utf8')) as { dialogue: { textColor: string } };
    expect(themeFile.dialogue.textColor).toBe('#ffcc00');

    // 非法颜色被拒绝，且不写入
    await page.getByTestId('theme-text-color').fill('not-a-color');
    await page.getByTestId('theme-save').click();
    await expect(page.getByTestId('message')).toContainText('修改主题被拒绝', { timeout: 15000 });
    const still = JSON.parse(readFileSync(path.join(backend.workspace, projectId, 'themes', 'theme.json'), 'utf8')) as { dialogue: { textColor: string } };
    expect(still.dialogue.textColor).toBe('#ffcc00');

    // 导出发布包：展示器的 content.json 带主题，页面按主题渲染对白
    const exported = await api(backend, 'POST', `/v1/projects/${projectId}/exports`, {});
    expect(exported.status, JSON.stringify(exported.body)).toBe(201);
    const bundle = JSON.parse(readFileSync(path.join(backend.workspace, (exported.body as { directory: string }).directory, 'content', 'content.json'), 'utf8')) as { theme?: { dialogue: { textColor: string; fontSize: number } } };
    expect(bundle.theme?.dialogue.textColor).toBe('#ffcc00');
    expect(bundle.theme?.dialogue.fontSize).toBe(28);
    await page.screenshot({ path: 'test-results/studio-live-theme.png', fullPage: true });
  });

  test('生成模板面板：新建模板 → 后台与磁盘可见 → 提示词可改可删', async ({ page }) => {
    await page.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
    await expect(page.getByTestId('connection')).toContainText('后台已连接', { timeout: 20000 });

    await page.getByTestId('tab-recipes').click();
    await page.getByTestId('recipe-new-name').fill('雨夜塔门');
    await page.getByTestId('recipe-new-type').selectOption('image.text');
    await page.getByTestId('recipe-new-prompt').fill('雨夜的旧塔，青石台阶，电影感光效');
    await page.getByTestId('recipe-create').click();
    await expect(page.getByTestId('recipe-list')).toContainText('雨夜塔门', { timeout: 15000 });
    await expect(page.getByTestId('message')).toContainText('新建生成模板完成');

    // 后台 outline 与磁盘文件一致
    const outline = await api(backend, 'GET', `/v1/projects/${projectId}/outline`);
    const recipes = (outline.body as { recipes?: { recipeId: string; name: string; taskType: string; prompt: string }[] }).recipes ?? [];
    const created = recipes.find((recipe) => recipe.name === '雨夜塔门');
    expect(created?.taskType).toBe('image.text');
    const recipeFile = JSON.parse(readFileSync(path.join(backend.workspace, projectId, 'generation', 'recipes', `${created!.recipeId}.json`), 'utf8')) as { prompt: string };
    expect(recipeFile.prompt).toContain('青石台阶');

    // 改提示词（走同一事务入口）
    await page.getByTestId(`recipe-prompt-${created!.recipeId}`).fill('雨夜的旧塔，暖色灯笼');
    await page.getByTestId(`recipe-prompt-${created!.recipeId}`).blur();
    await expect(page.getByTestId('message')).toContainText('修改生成模板完成', { timeout: 15000 });
    const afterEdit = await api(backend, 'GET', `/v1/projects/${projectId}/outline`);
    expect(((afterEdit.body as { recipes?: { recipeId: string; prompt: string }[] }).recipes ?? []).find((recipe) => recipe.recipeId === created!.recipeId)?.prompt).toBe('雨夜的旧塔，暖色灯笼');

    // 非法任务类型被拒绝（绕过界面直接提交，验证后台校验）
    const rejected = await api(backend, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: `e2e-recipe-bad-${Date.now()}`,
      projectId,
      baseRevision: (afterEdit.body as { revision: string }).revision,
      operations: [{ action: 'recipe.create', payload: { name: '坏模板', taskType: 'audio.text', prompt: 'x' } }],
    });
    expect((rejected.body as { status: string }).status).toBe('rejected');

    // 删除模板
    await page.getByTestId(`recipe-delete-${created!.recipeId}`).click();
    await expect(page.getByTestId('message')).toContainText('删除生成模板完成', { timeout: 15000 });
    const finalOutline = await api(backend, 'GET', `/v1/projects/${projectId}/outline`);
    expect(((finalOutline.body as { recipes?: { recipeId: string }[] }).recipes ?? []).some((recipe) => recipe.recipeId === created!.recipeId)).toBe(false);
    await page.screenshot({ path: 'test-results/studio-live-recipes.png', fullPage: true });
  });

  test('切换工程后重新订阅，当前工程的 AI 修改自动显示，旧工程消息不混入（KI-039）', async ({ page }) => {
    const created = await api(backend, 'POST', '/v1/projects', { title: '切换订阅工程 B' });
    const secondId = created.body.projectId as string;
    const secondScene = created.body.sceneId as string;
    await page.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
    await expect(page.getByTestId('project-select')).toHaveValue(projectId);
    await page.getByTestId('project-select').selectOption(secondId);
    await expect(page.getByTestId(`scene-${secondScene}`)).toBeVisible();
    const updated = await api(backend, 'POST', `/v1/projects/${secondId}/transactions`, {
      requestId: 'switch-project-b', baseRevision: created.body.revision,
      operations: [{ action: 'event.insert', payload: { sceneId: secondScene, type: 'dialogue', params: { text: '切换后的 AI 实时更新' } } }],
      summary: '当前工程 B 的变更',
    });
    expect(updated.body.status).toBe('applied');
    await expect(page.getByTestId('event-list')).toContainText('切换后的 AI 实时更新', { timeout: 15000 });
    const currentA = await api(backend, 'GET', `/v1/projects/${projectId}`);
    const old = await api(backend, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'switch-old-project-a', baseRevision: currentA.body.revision,
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'wait', params: { ms: 20 } } }],
      summary: '旧工程 A 的消息',
    });
    expect(old.body.status).toBe('applied');
    revision = old.body.revision as string;
    // 切回 A，再次确认订阅能随工程往返切换。
    await page.getByTestId('project-select').selectOption(projectId);
    await expect(page.getByTestId(`scene-${sceneId}`)).toBeVisible();
    const final = await api(backend, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: 'switch-back-a', baseRevision: revision,
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { text: '切回 A 仍能实时更新' } } }],
      summary: '切回 A 的变更',
    });
    expect(final.body.status).toBe('applied');
    revision = final.body.revision as string;
    await expect(page.getByTestId('event-list')).toContainText('切回 A 仍能实时更新', { timeout: 15000 });
    await expect(page.getByTestId('change-log')).not.toContainText('当前工程 B 的变更');
    await page.screenshot({ path: 'test-results/studio-live-project-switch.png', fullPage: true });
  });

  test('人工插入事件并暂停 AI 会话写入', async ({ page }) => {
    await page.goto(`/studio-live.html?api=${encodeURIComponent(backend.url)}`);
    await expect(page.getByTestId('connection')).toContainText('后台已连接', { timeout: 20000 });
    await page.getByTestId('insert-type').selectOption('dialogue');
    await page.getByTestId('insert-event').click();
    await expect(page.getByTestId('param-text')).toHaveValue('新的对白（人工插入）');
    await page.getByTestId('submit-draft').click();
    await expect(page.getByTestId('message')).toContainText('已插入事件', { timeout: 15000 });
    await expect(page.getByTestId('event-list')).toContainText('新的对白（人工插入）');

    // 暂停 e2e-ai 会话后，AI 写入必须被后台拒绝
    await page.locator('details.advanced summary').click();
    const sessions = await page.getByTestId('ai-sessions').locator('li').count();
    expect(sessions).toBeGreaterThan(0);
    await page.getByTestId('ai-sessions').locator('button').first().click();
    await expect(page.getByTestId('ai-sessions')).toContainText('恢复写入', { timeout: 15000 });
    const snapshot = await api(backend, 'GET', `/v1/projects/${projectId}`);
    const blocked = await api(backend, 'POST', `/v1/projects/${projectId}/transactions`, {
      requestId: `e2e-paused-${Date.now()}`,
      projectId,
      baseRevision: snapshot.body.revision,
      operations: [{ action: 'event.insert', payload: { sceneId, type: 'wait', params: { ms: 10 } } }],
    });
    expect(blocked.status).toBe(423);
    const beforeJobs = await api(backend, 'GET', `/v1/projects/${projectId}/media-jobs`);
    const blockedMedia = await api(backend, 'POST', `/v1/projects/${projectId}/media-jobs`, {
      requestId: 'e2e-paused-media', providerId: 'fixture-local', modelId: 'fixture-image-1', kind: 'image.text', prompt: '暂停后不能生成', output: { count: 1 },
    });
    expect(blockedMedia.status).toBe(423);
    expect((blockedMedia.body.error as { code: string }).code).toBe('SESSION_PAUSED');
    const afterJobs = await api(backend, 'GET', `/v1/projects/${projectId}/media-jobs`);
    expect(afterJobs.body.jobs).toEqual(beforeJobs.body.jobs);
    const beforeBudget = beforeJobs.body.budget as { reserved: number; spent: number };
    const afterBudget = afterJobs.body.budget as typeof beforeBudget;
    expect(afterBudget.reserved).toBe(beforeBudget.reserved);
    expect(afterBudget.spent).toBe(beforeBudget.spent);
  });
});
