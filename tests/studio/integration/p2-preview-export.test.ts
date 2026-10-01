// P2 集成测试：无界面预览 API（与展示器共用运行内核）与发布包导出/完整性检查。
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { decodeAsset } from '../../../server/src/media/decode.ts';
import path from 'node:path';
import { applyRuntimeAction, startRuntime } from '../../../src/runtime/kernel.ts';
import type { StoryContent } from '../../../src/runtime/content.ts';
import { call, buildStory } from '../helpers/story.ts';
import type { StoryFixture } from '../helpers/story.ts';
import { api, makeWorkspace, pairSession, readPairingKey, startBackend, trace } from '../helpers/harness.ts';
import type { ServiceHandle } from '../helpers/harness.ts';

let workspace = '';
let service: ServiceHandle;
let token = '';
let story: StoryFixture;

beforeAll(async () => {
  workspace = makeWorkspace('p2-preview');
  service = await startBackend(workspace, { name: 'p2-preview' });
  const session = await pairSession(service.url, readPairingKey(workspace), 'p2-preview-ai');
  token = session.token;
  story = await buildStory(service.url, token, workspace);
});

afterAll(async () => {
  await service?.stop();
});

describe('P2 无界面预览', () => {
  let previewId = '';

  it('开始预览返回入口场景、修订与首条对白', async () => {
    const response = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews`, {});
    expect(response.status).toBe(201);
    const body = response.body as { previewId: string; revision: string; status: string; sceneId: string; lineCount: number };
    previewId = body.previewId;
    expect(body.status).toBe('awaitingAdvance');
    expect(body.sceneId).toBe(story.entrySceneId);
    expect(body.revision).toBe(story.revision);
    expect(body.lineCount).toBe(1);
    trace('p2-preview-start', { previewId, revision: body.revision, sceneId: body.sceneId });
  });

  it('推进到选择，选择后进入关卡场景', async () => {
    const advanced = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews/${previewId}/actions`, { type: 'advance' });
    expect((advanced.body as { status: string }).status).toBe('awaitingChoice');
    const choice = (advanced.body as { pendingChoice: { options: { label: string }[] } }).pendingChoice;
    expect(choice.options.map((option) => option.label)).toEqual(['进塔']);

    const chosen = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews/${previewId}/actions`, { type: 'choose', option: 0 });
    const body = chosen.body as { status: string; sceneId: string };
    expect(body.sceneId).toBe(story.levelSceneId);
    expect(body.status).toBe('awaitingAdvance');

    const toLevel = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews/${previewId}/actions`, { type: 'advance' });
    const levelBody = toLevel.body as { status: string; pendingEffect: { kind: string; payload: { levelId: string } } };
    expect(levelBody.status).toBe('awaitingEffect');
    expect(levelBody.pendingEffect.kind).toBe('level');
    expect(levelBody.pendingEffect.payload.levelId).toBe(story.levelId);
  });

  it('关卡完成消息推进到结局，重复完成消息不重复推进', async () => {
    const complete = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews/${previewId}/actions`, {
      type: 'levelMessage',
      message: { kind: 'complete', levelInstanceId: 'lvl_test', requestId: 'done-1', messageSeq: 2, payload: { solved: true } },
    });
    const body = complete.body as { status: string; ended: { endingId: string; title: string }; errors: unknown[] };
    expect(body.status).toBe('ended');
    expect(body.ended.endingId).toBe('end_tower');
    expect(body.ended.title).toBe('塔顶');
    expect(body.errors).toEqual([]);

    const duplicate = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews/${previewId}/actions`, {
      type: 'levelMessage',
      message: { kind: 'complete', levelInstanceId: 'lvl_test', requestId: 'done-1', messageSeq: 2, payload: { solved: true, reward: 'duplicate' } },
    });
    expect((duplicate.body as { status: string }).status).toBe('ended');
    expect((duplicate.body as { lineCount: number }).lineCount).toBe(2);
  });

  it('渲染产物写出结构化舞台快照文件', async () => {
    const rendered = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews/${previewId}/render`, {});
    expect(rendered.status).toBe(200);
    const body = rendered.body as { renderPath: string; sha256: string; htmlLength: number; note: string };
    expect(body.renderPath).toMatch(/^\.studio\/previews\/.*\.html$/);
    expect(body.htmlLength).toBeGreaterThan(200);
    expect(body.note).toContain('不是像素级渲染');
    const abs = path.join(workspace, story.projectId, body.renderPath);
    expect(existsSync(abs)).toBe(true);
    const html = readFileSync(abs, 'utf8');
    expect(html).toContain('门后是向上的石阶。'); // 快照展示当前对白
    expect(html).toContain('背景：as_p2bg000001');
    expect(html).toContain('不是像素级渲染结果');
    trace('p2-preview-render', { renderPath: body.renderPath, sha256: body.sha256, htmlLength: body.htmlLength });
  });

  it('工程修订变化后旧预览会话拒绝继续推进', async () => {
    const bumped = await api(service.url, token, 'POST', `/v1/projects/${story.projectId}/transactions`, {
      requestId: 'p2-bump',
      projectId: story.projectId,
      baseRevision: story.revision,
      operations: [{ action: 'event.insert', payload: { sceneId: story.levelSceneId, type: 'wait', params: { ms: 10 } } }],
    });
    expect((bumped.body as { status: string }).status).toBe('applied');
    story.revision = (bumped.body as { revision: string }).revision;

    const rejected = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews/${previewId}/actions`, { type: 'advance' });
    expect(rejected.status).toBe(409);
    expect((rejected.body as { error: { code: string } }).error.code).toBe('CONFLICT');
  });

  it('不存在的场景无法开始预览', async () => {
    const response = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews`, { sceneId: 'sc_missing0001' });
    expect(response.status).toBe(404);
    expect((response.body as { error: { code: string } }).error.code).toBe('NOT_FOUND');
  });

  it('预览服务不引入自己的推进逻辑：结果与直接运行共用内核一致', async () => {
    // 用同一份内容直接在测试进程里跑内核，逐步与预览 API 的结果比较。
    const snapshot = await call(service.url, token, 'GET', `/v1/projects/${story.projectId}`);
    expect(snapshot.status).toBe(200);
    const outline = await call(service.url, token, 'GET', `/v1/projects/${story.projectId}/outline`);
    const sceneIds = (outline.body as { outline: { scenes: { sceneId: string }[] }[] }).outline.flatMap((chapter) => chapter.scenes.map((scene) => scene.sceneId));
    const scenes: StoryContent['scenes'] = [];
    for (const sceneId of sceneIds) {
      const detail = await call(service.url, token, 'GET', `/v1/projects/${story.projectId}/scenes/${sceneId}`);
      const scene = (detail.body as unknown as { scene: StoryContent['scenes'][number] }).scene;
      scenes.push(scene);
    }
    const outlineBody = outline.body as { variables: StoryContent['variables']; characters: StoryContent['characters']; levels: StoryContent['levels'] };
    const assets = (await call(service.url, token, 'GET', `/v1/projects/${story.projectId}/assets`)).body as { assets: StoryContent['assets'] };
    const projectBody = snapshot.body as { project: StoryContent['project'] };
    const local: StoryContent = {
      project: projectBody.project,
      chapters: (outline.body as { outline: { chapterId: string; title: string; scenes: { sceneId: string; title: string }[] }[] }).outline.map((chapter, index) => ({
        chapterId: chapter.chapterId, title: chapter.title, order: index, sceneIds: chapter.scenes.map((scene) => scene.sceneId),
      })),
      scenes,
      variables: outlineBody.variables,
      assets: assets.assets,
      characters: outlineBody.characters,
      levels: outlineBody.levels,
    };
    const revision = (snapshot.body as { revision: string }).revision;
    const started = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews`, { sceneId: story.entrySceneId });
    const startBody = started.body as { previewId: string; status: string; sceneId: string };
    const previewId = startBody.previewId;

    let localState = startRuntime(local, story.entrySceneId, { revision });
    // 起点状态也必须一致
    expect(`start:${startBody.status}:${startBody.sceneId}`).toBe(`start:${localState.status}:${localState.sceneId}`);

    const localSteps: string[] = [];
    const actions: { type: string }[] = [
      { type: 'advance' },
      { type: 'choose' },
      { type: 'advance' },
      { type: 'levelMessage' },
    ];
    localState = applyRuntimeAction(local, localState, { type: 'advance' });
    localSteps.push(`advance:${localState.status}:${localState.sceneId}`);
    localState = applyRuntimeAction(local, localState, { type: 'choose', option: 0 });
    localSteps.push(`choose:${localState.status}:${localState.sceneId}`);
    localState = applyRuntimeAction(local, localState, { type: 'advance' });
    localSteps.push(`advance:${localState.status}:${localState.sceneId}`);
    localState = applyRuntimeAction(local, localState, { type: 'levelMessage', message: { kind: 'complete', levelInstanceId: 'lvl_cmp', requestId: 'cmp-done', messageSeq: 1, payload: { solved: true } } });
    localSteps.push(`levelMessage:${localState.status}:${localState.sceneId}`);

    const serviceSteps: string[] = [];
    const requestBodies: Record<string, unknown>[] = [
      { type: 'advance' },
      { type: 'choose', option: 0 },
      { type: 'advance' },
      { type: 'levelMessage', message: { kind: 'complete', levelInstanceId: 'lvl_cmp', requestId: 'cmp-done', messageSeq: 1, payload: { solved: true } } },
    ];
    for (let index = 0; index < requestBodies.length; index += 1) {
      const response = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews/${previewId}/actions`, requestBodies[index]);
      const body = response.body as { status: string; sceneId: string };
      serviceSteps.push(`${actions[index].type}:${body.status}:${body.sceneId}`);
    }
    expect(serviceSteps).toEqual(localSteps);
    expect(localState.ended?.endingId).toBe('end_tower');
    const finalSummary = await call(service.url, token, 'GET', `/v1/projects/${story.projectId}/previews/${previewId}`);
    expect((finalSummary.body as { ended: { endingId: string } }).ended.endingId).toBe(localState.ended?.endingId);
    trace('p2-kernel-consistency', { localSteps, serviceSteps, endingId: localState.ended?.endingId });
  });
});

describe('P2 像素级渲染产物（可选能力）', () => {
  it('format=pixel 生成真实 PNG，尺寸与散列可核对；不可用时给出明确错误', async () => {
    const preview = await api(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews`, {});
    expect(preview.status).toBe(201);
    const previewId = (preview.body as { previewId: string }).previewId;

    const rendered = await api(service.url, token, 'POST', `/v1/projects/${story.projectId}/previews/${previewId}/render`, { format: 'pixel', width: 640, height: 360 });
    if (rendered.status === 503) {
      // 环境没有可用浏览器：必须是明确的能力不可用错误，而不是伪造产物
      expect((rendered.body as { error?: { code?: string } }).error?.code).toBe('RENDER_UNAVAILABLE');
      trace('p2-pixel-render', { skipped: 'browser unavailable', body: rendered.body });
      return;
    }
    expect(rendered.status, JSON.stringify(rendered.body)).toBe(200);
    const pixel = (rendered.body as { pixel?: { renderPath: string; sha256: string; width: number; height: number; bytes: number; renderer: string } }).pixel;
    expect(pixel).toBeTruthy();
    expect(pixel!.width).toBe(640);
    expect(pixel!.height).toBe(360);
    expect(pixel!.bytes).toBeGreaterThan(100);

    // 产物是真实的 PNG（用同一套解码器核对尺寸），并且散列与文件一致
    const abs = path.join(workspace, story.projectId, pixel!.renderPath);
    expect(existsSync(abs)).toBe(true);
    const bytes = readFileSync(abs);
    expect(bytes.subarray(0, 8)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));
    const decoded = decodeAsset(bytes, 'background');
    expect(decoded.ok, JSON.stringify(decoded)).toBe(true);
    if (decoded.ok) {
      expect(decoded.width).toBe(640);
      expect(decoded.height).toBe(360);
    }
    const digest = createHash('sha256').update(bytes).digest('hex');
    expect(digest).toBe(pixel!.sha256);
    trace('p2-pixel-render', { renderPath: pixel!.renderPath, bytes: pixel!.bytes, renderer: pixel!.renderer });
  }, 120000);

  it('禁用像素渲染时返回 RENDER_UNAVAILABLE（失败路径不伪造产物）', async () => {
    // 独立工作区：同一工作区只能有一个后台实例（单实例锁 + 发现文件），否则会连到已运行的服务上
    const disabledWorkspace = makeWorkspace('p2-render-disabled');
    const disabled = await startBackend(disabledWorkspace, { name: 'p2-render-disabled', env: { STUDIO_RENDER_DISABLE: '1' } });
    try {
      const session = await pairSession(disabled.url, readPairingKey(disabledWorkspace), 'p2-render-disabled');
      const created = await api(disabled.url, session.token, 'POST', '/v1/projects', { title: '禁用渲染工程', chapterTitle: '第一章', sceneTitle: '开场' });
      expect(created.status, JSON.stringify(created.body)).toBe(201);
      const projectId = (created.body as { projectId: string }).projectId;
      const preview = await api(disabled.url, session.token, 'POST', `/v1/projects/${projectId}/previews`, {});
      expect(preview.status).toBe(201);
      const previewId = (preview.body as { previewId: string }).previewId;
      const rendered = await api(disabled.url, session.token, 'POST', `/v1/projects/${projectId}/previews/${previewId}/render`, { format: 'pixel' });
      expect(rendered.status).toBe(503);
      expect((rendered.body as { error?: { code?: string; message?: string } }).error?.code).toBe('RENDER_UNAVAILABLE');
      // 结构化快照仍然可用（禁用只影响像素能力）
      const snapshot = await api(disabled.url, session.token, 'POST', `/v1/projects/${projectId}/previews/${previewId}/render`, {});
      expect(snapshot.status).toBe(200);
      expect((snapshot.body as { htmlLength?: number }).htmlLength).toBeGreaterThan(0);
    } finally {
      await disabled.stop();
    }
  }, 120000);
});
describe('P2 发布包导出', () => {
  it('发布包含关卡的嵌套脚本、样式与 JSON 数据依赖', async () => {
    const { mkdirSync, writeFileSync } = await import('node:fs');
    const deps = path.join(workspace, story.projectId, 'levels', 'nested', 'data');
    mkdirSync(deps, { recursive: true });
    writeFileSync(path.join(deps, 'settings.json'), '{"steps":3}');
    writeFileSync(path.join(deps, 'game.js'), 'window.levelReady = true;');
    writeFileSync(path.join(deps, 'theme.css'), 'body{color:white}');
    const exported = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/exports`, {});
    expect(exported.status).toBe(201);
    const exportedRoot = path.join(workspace, exported.body.directory as string, 'content/levels/nested/data');
    expect(readFileSync(path.join(exportedRoot, 'settings.json'), 'utf8')).toBe('{"steps":3}');
    expect(readFileSync(path.join(exportedRoot, 'game.js'), 'utf8')).toContain('levelReady');
    expect(readFileSync(path.join(exportedRoot, 'theme.css'), 'utf8')).toContain('color:white');
    trace('p2-export-nested-level-files', { count: 3, includesJsonDependency: true });
  });

  it('导出发布包并检查完整性、敏感信息与素材缺失', async () => {
    // 展示器构建产物必须先存在（npm run build:player）
    const exported = await call(service.url, token, 'POST', `/v1/projects/${story.projectId}/exports`, {});
    expect(exported.status).toBe(201);
    const body = exported.body as {
      exportId: string; directory: string; playerIncluded: boolean; sensitiveHits: string[]; missingAssets: string[]; fileCount: number; contentDigest: string; serveCommand: string;
    };
    expect(body.playerIncluded).toBe(true);
    expect(body.sensitiveHits).toEqual([]);
    expect(body.missingAssets).toEqual([]);
    expect(body.fileCount).toBeGreaterThan(5);
    expect(body.contentDigest).toMatch(/^[0-9a-f]{64}$/);

    const dir = path.join(workspace, body.directory);
    for (const rel of ['manifest.json', 'serve.mjs', 'content/content.json', 'player.html']) {
      expect(existsSync(path.join(dir, rel)), `缺少 ${rel}`).toBe(true);
    }
    const manifest = JSON.parse(readFileSync(path.join(dir, 'manifest.json'), 'utf8')) as { revision: string; files: { rel: string }[]; notes: string[] };
    expect(manifest.revision).toBe(story.revision);
    expect(manifest.files.some((file) => file.rel.includes('assets/originals/as_p2bg000001.png'))).toBe(true);
    expect(manifest.files.some((file) => file.rel.startsWith('.studio'))).toBe(false);
    expect(manifest.files.some((file) => /pairing\.key|sessions\.json|credentials/i.test(file.rel))).toBe(false);
    trace('p2-export', { exportId: body.exportId, directory: body.directory, fileCount: body.fileCount, contentDigest: body.contentDigest, sensitiveHits: body.sensitiveHits });
  });

  it('发布包内容包含关卡程序与登记素材，且内容包记录了修订', async () => {
    const listing = await call(service.url, token, 'GET', `/v1/projects/${story.projectId}/exports`);
    const exports = (listing.body as { exports: { exportId: string; revision: string; sensitiveHits: string[] }[] }).exports;
    expect(exports.length).toBeGreaterThan(0);
    const latest = exports[0];
    expect(latest.sensitiveHits).toEqual([]);
    const dir = path.join(workspace, story.projectId, 'exports', latest.exportId);
    const bundle = JSON.parse(readFileSync(path.join(dir, 'content', 'content.json'), 'utf8')) as { revision: string; scenes: { sceneId: string }[]; levels: { levelId: string }[] };
    expect(bundle.revision).toBe(latest.revision);
    expect(bundle.scenes.map((scene) => scene.sceneId)).toContain(story.levelSceneId);
    expect(bundle.levels.map((level) => level.levelId)).toContain(story.levelId);
    expect(existsSync(path.join(dir, 'content', 'levels', 'lv_p2echo0001.html'))).toBe(true);
  });
});
