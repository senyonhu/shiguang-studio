import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import type { EditResult } from '../../../server/src/edit/service.ts';
import { encodeSolidPng } from '../../../server/src/media/decode.ts';
import { api, makeWorkspace, pairSession, readPairingKey, startBackend } from '../helpers/harness.ts';
import type { ServiceHandle } from '../helpers/harness.ts';

let workspace: string;
let service: ServiceHandle;
let humanToken: string;
let aiToken: string;
const image = encodeSolidPng(4, 3, [100, 120, 140]);
const html = '<!doctype html><html><body><p>人工上传关卡</p></body></html>';

beforeAll(async () => {
  workspace = makeWorkspace('human-ui-delivery');
  service = await startBackend(workspace, { name: 'human-ui-delivery' });
  const ui = await fetch(`${service.url}/v1/ui-session`, { method: 'POST', headers: { Origin: 'http://127.0.0.1:5190', 'content-type': 'application/json' } });
  expect(ui.status).toBe(201);
  humanToken = ((await ui.json()) as { token: string }).token;
  aiToken = (await pairSession(service.url, readPairingKey(workspace), 'delivery-ai')).token;
});
afterAll(async () => { await service?.stop(); });

async function newProject() {
  const response = await api(service.url, humanToken, 'POST', '/v1/projects', { title: '人工入口测试' });
  expect(response.status).toBe(201);
  return response.body as { projectId: string; revision: string; sceneId: string; chapterId: string };
}
function upload(project: { revision: string }, requestId: string, extra: Record<string, unknown> = {}) {
  return { baseRevision: project.revision, requestId, kind: 'background', name: '上传背景', fileName: '背景.png', dataBase64: image.toString('base64'), ...extra };
}

describe('人工文件上传入口', () => {
  it('导入图片、重试幂等、冲突后重试不重复，并清除暂存文件', async () => {
    const project = await newProject();
    const route = `/v1/projects/${project.projectId}/uploads`;
    const request = upload(project, 'upload-image');
    const first = await api(service.url, humanToken, 'POST', route, request);
    expect(first.status).toBe(200);
    const saved = first.body as EditResult & { assetId: string };
    expect(saved.status).toBe('applied');
    const replay = await api(service.url, humanToken, 'POST', route, request);
    expect((replay.body as EditResult).replayed).toBe(true);
    const assets = await api(service.url, humanToken, 'GET', `/v1/projects/${project.projectId}/assets`);
    expect((assets.body as { assets: unknown[] }).assets).toHaveLength(1);
    const conflictRequest = upload(project, 'upload-after-conflict', { name: '新背景' });
    const conflict = await api(service.url, humanToken, 'POST', route, conflictRequest);
    expect((conflict.body as EditResult).status).toBe('conflict');
    expect((await api(service.url, humanToken, 'GET', `/v1/projects/${project.projectId}/assets`)).body).toMatchObject({ assets: [{ assetId: saved.assetId }] });
    const retried = await api(service.url, humanToken, 'POST', route, { ...conflictRequest, baseRevision: saved.revision });
    expect((retried.body as EditResult).status).toBe('applied');
    expect((await api(service.url, humanToken, 'GET', `/v1/projects/${project.projectId}/assets`)).body).toMatchObject({ assets: expect.any(Array) });
    expect(((await api(service.url, humanToken, 'GET', `/v1/projects/${project.projectId}/assets`)).body as { assets: unknown[] }).assets).toHaveLength(2);
    expect(readdirSync(path.join(workspace, 'imports')).filter((name) => name.startsWith('ui-'))).toEqual([]);
    const changed = await api(service.url, humanToken, 'POST', route, { ...request, dataBase64: encodeSolidPng(2, 2, [1, 2, 3]).toString('base64') });
    expect(changed.status).toBe(409);
    expect(changed.body).toMatchObject({ error: { code: 'REQUEST_ID_CONFLICT' } });
  });

  it('拒绝非人工会话、路径穿越、非法Base64与不可解码文件', async () => {
    const project = await newProject();
    const route = `/v1/projects/${project.projectId}/uploads`;
    expect((await api(service.url, aiToken, 'POST', route, upload(project, 'not-human'))).status).toBe(403);
    for (const fileName of ['../x.png', '..\\x.png', 'C:\\x.png', 'file:stream.png']) {
      expect((await api(service.url, humanToken, 'POST', route, upload(project, 'bad-path', { fileName }))).status).toBe(400);
    }
    expect((await api(service.url, humanToken, 'POST', route, upload(project, 'bad-base64', { dataBase64: 'not base64===' }))).status).toBe(400);
    const invalid = await api(service.url, humanToken, 'POST', route, upload(project, 'not-image', { dataBase64: Buffer.from('not an image').toString('base64') }));
    expect(invalid.body).toMatchObject({ status: 'rejected' });
    expect(((await api(service.url, humanToken, 'GET', `/v1/projects/${project.projectId}/assets`)).body as { assets: unknown[] }).assets).toEqual([]);
  });

  it('一次事务登记并安装HTML关卡，指定关卡可替换程序，重试不重复', async () => {
    const project = await newProject();
    const route = `/v1/projects/${project.projectId}/uploads`;
    const request = upload(project, 'upload-level', { kind: 'level', name: '初始关卡', fileName: '关卡.html', dataBase64: Buffer.from(html).toString('base64'), supportsSnapshot: false });
    const response = await api(service.url, humanToken, 'POST', route, request);
    const first = response.body as EditResult & { levelId: string };
    expect(first.status).toBe('applied');
    const program = path.join(workspace, project.projectId, 'levels', `${first.levelId}.html`);
    expect(readFileSync(program, 'utf8')).toBe(html);
    expect((await api(service.url, humanToken, 'POST', route, request)).body).toMatchObject({ replayed: true, levelId: first.levelId });
    const replacement = await api(service.url, humanToken, 'POST', route, { ...request, requestId: 'replace-level', baseRevision: first.revision, levelId: first.levelId, name: '更新关卡', dataBase64: Buffer.from('<html><body>更新后的关卡</body></html>').toString('base64') });
    expect(replacement.body).toMatchObject({ status: 'applied', levelId: first.levelId });
    expect(readFileSync(program, 'utf8')).toContain('更新后的关卡');
    const outline = (await api(service.url, humanToken, 'GET', `/v1/projects/${project.projectId}/outline`)).body as { levels: { levelId: string; title: string }[] };
    expect(outline.levels).toEqual([expect.objectContaining({ levelId: first.levelId, title: '更新关卡' })]);
  });
});

describe('真实展示器播放和交付', () => {
  it('试玩文件固定到请求修订，后续替换关卡不会把旧链接变成新程序', async () => {
    const project = await newProject();
    const route = `/v1/projects/${project.projectId}/uploads`;
    const request = upload(project, 'snapshot-level', { kind: 'level', name: '快照关卡', fileName: '关卡.html', dataBase64: Buffer.from(html).toString('base64') });
    const installed = (await api(service.url, humanToken, 'POST', route, request)).body as EditResult & { levelId: string };
    expect(installed.status).toBe('applied');
    const originalLink = (await api(service.url, humanToken, 'POST', `/v1/projects/${project.projectId}/playback`, {})).body as { url: string };
    const programPath = `content/levels/${installed.levelId}.html`;
    const originalUrl = new URL(programPath, new URL('./', originalLink.url));
    expect(await (await fetch(originalUrl)).text()).toBe(html);
    const replacement = '<html><body>仅供新修订的关卡</body></html>';
    const changed = (await api(service.url, humanToken, 'POST', route, { ...request, requestId: 'snapshot-replace', baseRevision: installed.revision, levelId: installed.levelId, dataBase64: Buffer.from(replacement).toString('base64') })).body as EditResult;
    expect(changed.status).toBe('applied');
    expect(await (await fetch(originalUrl)).text()).toBe(html);
    const newLink = (await api(service.url, humanToken, 'POST', `/v1/projects/${project.projectId}/playback`, {})).body as { url: string };
    expect(await (await fetch(new URL(programPath, new URL('./', newLink.url)))).text()).toBe(replacement);
  });
  it('播放当前场景、读取真实播放器和素材，票据不能读取工作区或调用API', async () => {
    const project = await newProject();
    const saved = (await api(service.url, humanToken, 'POST', `/v1/projects/${project.projectId}/uploads`, upload(project, 'play-image'))).body as EditResult & { assetId: string };
    const newScene = await api(service.url, humanToken, 'POST', `/v1/projects/${project.projectId}/transactions`, { requestId: 'play-scene', baseRevision: saved.revision, operations: [{ action: 'scene.create', payload: { chapterId: project.chapterId, sceneId: 'sc_previewtest1', title: '当前场景' } }] });
    expect(newScene.body).toMatchObject({ status: 'applied' });
    const playback = await api(service.url, humanToken, 'POST', `/v1/projects/${project.projectId}/playback`, { sceneId: 'sc_previewtest1' });
    expect(playback.status).toBe(201);
    const result = playback.body as { url: string; revision: string; expiresAt: string };
    expect(result.url).not.toContain(humanToken);
    const base = new URL('./', result.url);
    const player = await fetch(result.url);
    expect(player.status).toBe(200);
    const page = await player.text();
    expect(page).toContain('player-root');
    const script = /src="([^"]+\.js)"/.exec(page)?.[1];
    expect(script).toBeTruthy();
    expect((await fetch(new URL(script!, base))).status).toBe(200);
    const bundle = await fetch(new URL('content/content.json', base)).then((response) => response.json()) as { project: { entrySceneId: string }; assets: { path: string }[] };
    expect(bundle.project.entrySceneId).toBe('sc_previewtest1');
    const bytes = await fetch(new URL(`content/${bundle.assets[0].path}`, base), { headers: { Range: 'bytes=0-7' } });
    expect(bytes.status).toBe(206);
    expect(Buffer.from(await bytes.arrayBuffer())).toEqual(image.subarray(0, 8));
    expect((await fetch(new URL('content/.studio/state.json', base))).status).toBe(404);
    expect((await fetch(new URL('runtime/node.exe', base))).status).toBe(404);
    expect((await fetch(new URL('content/levels/%2e%2e%2fproject.json', base))).status).toBe(404);
    expect((await fetch(result.url, { method: 'POST' })).status).toBe(403);
    expect((await fetch(`${service.url}/v1/projects`)).status).toBe(401);
    expect((await api(service.url, humanToken, 'GET', `/v1/projects/${project.projectId}`)).body).toMatchObject({ project: { entrySceneId: project.sceneId } });
  });

  it('缺少登记关卡的入口会拒绝导出和播放，不留下成功导出目录', async () => {
    const project = await newProject();
    const registered = await api(service.url, humanToken, 'POST', `/v1/projects/${project.projectId}/transactions`, { requestId: 'missing-level', baseRevision: project.revision, operations: [{ action: 'level.register', payload: { levelId: 'lv_missingentry', title: '未安装关卡', entry: 'levels/missing.html' } }] });
    expect(registered.body).toMatchObject({ status: 'applied' });
    for (const action of ['exports', 'playback']) {
      const response = await api(service.url, humanToken, 'POST', `/v1/projects/${project.projectId}/${action}`, {});
      expect(response.status).toBe(422);
      expect(response.body).toMatchObject({ error: { code: 'VALIDATION_FAILED', details: { missingLevels: ['lv_missingentry'] } } });
    }
    const directory = path.join(workspace, project.projectId, 'exports');
    expect(existsSync(directory) ? readdirSync(directory) : []).toEqual([]);
  });

  it('导出回传真实便携能力，导出后可直接播放且不会暴露运行环境或凭据', async () => {
    const project = await newProject();
    const exported = await api(service.url, humanToken, 'POST', `/v1/projects/${project.projectId}/exports`, {});
    expect(exported.status).toBe(201);
    const result = exported.body as { exportId: string; directory: string; portableIncluded: boolean; missingLevels: string[] };
    expect(result.missingLevels).toEqual([]);
    expect(result.portableIncluded).toBe(existsSync(path.join(workspace, result.directory, '启动展示器.cmd')));
    const played = await api(service.url, humanToken, 'POST', `/v1/projects/${project.projectId}/exports/${result.exportId}/playback`, {});
    expect(played.status).toBe(201);
    const base = new URL('./', (played.body as { url: string }).url);
    expect((await fetch(new URL('content/content.json', base))).status).toBe(200);
    for (const relative of ['serve.mjs', 'manifest.json', 'portable.json', 'runtime/node.exe', 'data/launcher-state.json']) {
      expect((await fetch(new URL(relative, base))).status).toBe(404);
    }
    expect((await api(service.url, aiToken, 'POST', `/v1/projects/${project.projectId}/exports/${result.exportId}/open-directory`, {})).status).toBe(403);
  });
});
