// Isolated source-player regressions. Browser routes supply private fixture bundles;
// no real workspace, Demo, exported package, or installed content is modified.
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import path from 'node:path';
import type { EventRecord, StoryContent } from '../../src/runtime/content.ts';
import { DEFAULT_THEME } from '../../src/runtime/content.ts';

function story(events: EventRecord[], projectId = 'pr_audit'): StoryContent {
  return {
    project: { projectId, title: '展示器隔离审计', formatVersion: 1, entrySceneId: 'sc_audit', chapterIds: ['ch_audit'], createdAt: '', updatedAt: '', runtimeCompat: 'studio-runtime/1' },
    chapters: [{ chapterId: 'ch_audit', title: '第一章', order: 0, sceneIds: ['sc_audit'] }],
    scenes: [{ sceneId: 'sc_audit', chapterId: 'ch_audit', title: '开场', events }],
    assets: [], variables: [], levels: [], characters: [], theme: DEFAULT_THEME,
  };
}
const dialogue = (id: string, text: string): EventRecord => ({ eventId: id, type: 'dialogue', params: { text } });
const ending: EventRecord = { eventId: 'ev_end', type: 'ending', params: { endingId: 'ending_audit', title: '审计结局' } };
function silentWav(seconds = 2): Buffer {
  const data = Buffer.alloc(8000 * seconds * 2), header = Buffer.alloc(44);
  header.write('RIFF', 0); header.writeUInt32LE(36 + data.length, 4); header.write('WAVE', 8); header.write('fmt ', 12); header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20); header.writeUInt16LE(1, 22); header.writeUInt32LE(8000, 24); header.writeUInt32LE(16000, 28); header.writeUInt16LE(2, 32); header.writeUInt16LE(16, 34); header.write('data', 36); header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}
function addAudio(content: StoryContent): void {
  content.assets.push({ assetId: 'as_audio', name: '提示音', kind: 'audio', path: 'assets/tone.wav', mimeType: 'audio/wav', sha256: 'a'.repeat(64), bytes: 10, version: '1', importedAt: '', sourceKind: 'file' });
}
async function mount(page: Page, content: StoryContent, name = 'content'): Promise<void> {
  await page.route((url) => url.pathname === `/__player-audit__/${name}.json`, (route) => route.fulfill({ json: { apiVersion: '1', kernelVersion: 1, revision: '0', projectId: content.project.projectId, ...content } }));
  await page.route('**/__player-audit__/assets/tone.wav', (route) => route.fulfill({ body: silentWav(), contentType: 'audio/wav' }));
  await page.goto(`/player.html?content=/__player-audit__/${name}.json`);
  await expect(page.getByTestId('start')).toBeEnabled();
}
const levelProgram = (name: string): string => `<!doctype html><html><body><h1>${name}</h1><p id="snapshot"></p><button id="done">完成</button><script>
parent.postMessage({type:'ready'},'*');
window.addEventListener('message',event=>{if(event.data?.type!=='init')return;document.querySelector('#snapshot').textContent=JSON.stringify(event.data.snapshot);parent.postMessage({type:'checkpoint',requestId:'cp',messageSeq:1,payload:{source:'${name}'}},'*');});
document.querySelector('#done').onclick=()=>parent.postMessage({type:'complete',requestId:'done',messageSeq:2,payload:{solved:true}},'*');
</script></body></html>`;

test('连续关卡各执行一次，前一关卡快照不会流入下一关卡', async ({ page }) => {
  const content = story([{ eventId: 'ev_level_a', type: 'level', params: { levelId: 'lv_a' } }, { eventId: 'ev_level_b', type: 'level', params: { levelId: 'lv_b' } }, dialogue('ev_after', '两关都已完成'), ending]);
  content.levels = [{ levelId: 'lv_a', title: '第一关', entry: 'levels/a.html', supportsSnapshot: true }, { levelId: 'lv_b', title: '第二关', entry: 'levels/b.html', supportsSnapshot: true }];
  await page.route('**/__player-audit__/levels/a.html', (route) => route.fulfill({ body: levelProgram('第一关'), contentType: 'text/html' }));
  await page.route('**/__player-audit__/levels/b.html', (route) => route.fulfill({ body: levelProgram('第二关'), contentType: 'text/html' }));
  await mount(page, content);
  await page.getByTestId('start').click();
  await expect(page.frameLocator('[data-testid="level-frame"]').locator('h1')).toHaveText('第一关');
  await expect(page.frameLocator('[data-testid="level-frame"]').locator('#snapshot')).toHaveText('null');
  await expect(page.locator('#player-status')).toContainText('已记录进度点');
  await page.getByTestId('save').click();
  const firstSnapshot = await page.evaluate(() => JSON.parse(localStorage.getItem('shiguang-player-save-v1:pr_audit')!).levels.snapshot);
  expect(firstSnapshot).toEqual({ source: '第一关' });
  await page.frameLocator('[data-testid="level-frame"]').locator('#done').click();
  await expect(page.frameLocator('[data-testid="level-frame"]').locator('h1')).toHaveText('第二关');
  await expect(page.frameLocator('[data-testid="level-frame"]').locator('#snapshot')).toHaveText('null');
  await page.frameLocator('[data-testid="level-frame"]').locator('#done').click();
  await expect(page.getByTestId('dialogue')).toContainText('两关都已完成');
});

test('超过四秒的等待按真实时长推进，保存读档保留剩余等待', async ({ page }) => {
  await page.clock.install();
  await mount(page, story([{ eventId: 'ev_wait', type: 'wait', params: { ms: 6000 } }, dialogue('ev_after', '六秒等待之后')]));
  await page.getByTestId('start').click();
  await page.clock.runFor(2000);
  await page.getByTestId('save').click();
  await page.clock.runFor(2100);
  await expect(page.getByTestId('waiting')).toBeVisible();
  await page.getByTestId('load').click();
  await page.clock.runFor(4100);
  await expect(page.getByTestId('dialogue')).toContainText('六秒等待之后');
});

test('手动推进不会遗留自动播放定时器而跳过下一句', async ({ page }) => {
  await page.clock.install();
  await mount(page, story([dialogue('ev_one', '第一句'), dialogue('ev_two', '第二句'), dialogue('ev_three', '第三句')]));
  await page.getByTestId('start').click();
  await page.getByTestId('text-speed').selectOption('slow');
  await page.getByTestId('auto').click();
  await page.clock.runFor(500);
  await page.getByTestId('advance').click();
  await expect(page.getByTestId('dialogue')).toContainText('第二句');
  await page.clock.runFor(1800);
  await expect(page.getByTestId('dialogue')).toContainText('第二句');
  await page.clock.runFor(500);
  await expect(page.getByTestId('dialogue')).toContainText('第三句');
});

test('音频遵循循环和音量，停止声道后重新开始仍重新播放', async ({ page }) => {
  const content = story([
    { eventId: 'ev_audio', type: 'audio', params: { assetId: 'as_audio', channel: 'bgm', action: 'play', loop: false, volume: 0.25 } }, dialogue('ev_one', '播放中'),
    { eventId: 'ev_stop', type: 'audio', params: { assetId: 'as_audio', channel: 'bgm', action: 'stop' } }, dialogue('ev_two', '已停止'), ending,
  ]);
  addAudio(content);
  await mount(page, content);
  await page.getByTestId('start').click();
  await expect(page.getByTestId('audio-bgm')).toBeAttached();
  expect(await page.getByTestId('audio-bgm').evaluate((node) => (node as HTMLAudioElement).loop)).toBe(false);
  expect(await page.getByTestId('audio-bgm').evaluate((node) => (node as HTMLAudioElement).volume)).toBe(0.25);
  await page.getByTestId('advance').click();
  await expect(page.getByTestId('dialogue')).toContainText('已停止');
  await expect(page.getByTestId('audio-bgm')).toHaveCount(0);
  await page.getByTestId('advance').click();
  await page.getByTestId('restart').click();
  await page.getByTestId('start').click();
  await expect(page.getByTestId('audio-bgm')).toHaveCount(1);
});

test('对白读档不重复历史，结局标题恢复且不同作品存档互不覆盖', async ({ page }) => {
  const one = story([dialogue('ev_one', '作品一对白'), ending], 'pr_one');
  const two = story([dialogue('ev_one', '作品二对白'), ending], 'pr_two');
  await mount(page, one, 'one');
  await page.getByTestId('start').click();
  await page.getByTestId('save').click();
  await page.getByTestId('load').click();
  await expect(page.getByTestId('history')).toContainText('历史对白（1）');
  await page.getByTestId('advance').click();
  await page.getByTestId('save').click();
  await page.getByTestId('load').click();
  await expect(page.getByTestId('ending')).toContainText('审计结局');
  await mount(page, two, 'two');
  await expect(page.getByTestId('continue')).toBeDisabled();
  await page.getByTestId('start').click();
  await page.getByTestId('save').click();
  await mount(page, one, 'one');
  await page.getByTestId('continue').click();
  await expect(page.getByTestId('ending')).toContainText('审计结局');
});

test('视频的不可跳过与结束剧情参数实际生效', async ({ page }) => {
  const content = story([{ eventId: 'ev_bgm', type: 'audio', params: { assetId: 'as_audio', channel: 'bgm', action: 'play' } }, { eventId: 'ev_video', type: 'video', params: { assetId: 'as_video', skippable: false, onEnd: 'stop', onError: 'continue' } }, dialogue('ev_after', '不应继续')]);
  addAudio(content);
  content.assets.push({ assetId: 'as_video', name: '影像', kind: 'video', path: 'assets/video.mp4', mimeType: 'video/mp4', sha256: 'a'.repeat(64), bytes: 2000, version: '1', importedAt: '', sourceKind: 'file' });
  await page.route('**/__player-audit__/assets/video.mp4', (route) => route.fulfill({ body: readFileSync(path.join(process.cwd(), 'tests/studio/fixtures/sample-1s.mp4')), contentType: 'video/mp4' }));
  await mount(page, content);
  await page.getByTestId('start').click();
  await expect(page.getByTestId('video')).toBeAttached();
  await expect(page.getByTestId('audio-bgm')).toBeAttached();
  await expect(page.getByTestId('video-skip')).toHaveCount(0);
  await expect(page.getByTestId('ending')).toBeVisible({ timeout: 10000 });
  await expect(page.getByTestId('dialogue')).not.toContainText('不应继续');
});

test('背景人物变量选择跳转及主题在真实舞台中一致，跨场景事件目标可达', async ({ page }) => {
  const content = story([
    { eventId: 'ev_bg', type: 'background', params: { assetId: 'as_bg' } },
    { eventId: 'ev_char', type: 'character', params: { assetId: 'as_char', position: 'left', action: 'enter' } },
    { eventId: 'ev_var', type: 'variable', params: { variableId: 'vr_score', op: 'set', value: '12' } }, dialogue('ev_first', '123'),
    { eventId: 'ev_choice', type: 'choice', params: { prompt: '选择后继续', options: [{ label: '前往另一场景的中间事件', targetEventId: 'ev_middle' }] } }, ending,
  ]);
  content.scenes.push({ sceneId: 'sc_other', chapterId: 'ch_audit', title: '第二场景', events: [dialogue('ev_skip', '不应从场景开头开始'), dialogue('ev_middle', '跨场景引用已到达'), { eventId: 'ev_add', type: 'variable', params: { variableId: 'vr_score', op: 'add', value: '3' } }, { eventId: 'ev_exit', type: 'character', params: { assetId: 'as_char', position: 'left', action: 'exit' } }, { eventId: 'ev_jump', type: 'jump', params: { targetSceneId: 'sc_audit', targetEventId: 'ev_end' } }] });
  content.chapters[0]!.sceneIds.push('sc_other');
  content.variables = [{ variableId: 'vr_score', name: '得分', type: 'number', initial: 0, scope: 'global' }];
  content.characters = [{ characterId: 'cr_one', name: '作者' }];
  content.theme = { themeId: 'audit', dialogue: { textColor: '#123456', fontSize: 24, align: 'center' }, menu: { accentColor: '#654321' } };
  for (const [assetId, kind] of [['as_bg', 'background'], ['as_char', 'character']] as const) content.assets.push({ assetId, name: assetId, kind, path: `assets/${assetId}.png`, mimeType: 'image/png', sha256: 'a'.repeat(64), bytes: 68, version: '1', importedAt: '', sourceKind: 'file' });
  await page.route('**/__player-audit__/assets/*.png', (route) => route.fulfill({ body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aq1kAAAAASUVORK5CYII=', 'base64'), contentType: 'image/png' }));
  await mount(page, content);
  await page.getByTestId('start').click();
  await expect(page.getByTestId('dialogue')).toContainText('123');
  await expect(page.getByTestId('dialogue')).toHaveCSS('color', 'rgb(18, 52, 86)');
  await expect(page.getByTestId('dialogue')).toHaveCSS('font-size', '24px');
  await expect(page.getByTestId('character-as_char')).toHaveClass('character left');
  expect(await page.getByTestId('background').evaluate((node) => (node as HTMLImageElement).naturalWidth)).toBe(1);
  await page.getByTestId('advance').click();
  await expect(page.getByTestId('choices')).toContainText('选择后继续');
  await page.getByTestId('choice-0').click();
  await expect(page.getByTestId('dialogue')).toContainText('跨场景引用已到达');
  await page.getByTestId('advance').click();
  await expect(page.getByTestId('ending')).toContainText('审计结局');
  await expect(page.getByTestId('character-as_char')).toHaveCount(0);
  await page.getByTestId('save').click();
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('shiguang-player-save-v1:pr_audit')!));
  expect(saved.variables.vr_score).toBe(15);
  expect(saved.lines.map((line: { text: string }) => line.text)).toEqual(['123', '跨场景引用已到达']);
});

test('HTML关卡不能读取宿主或存档，允许相对JSON及明确C服务并保留宿主Origin', async ({ page }) => {
  const origins: string[] = [];
  const paths: string[] = [];
  const runner = createServer((request, response) => {
    paths.push(request.url!);
    response.setHeader('Access-Control-Allow-Origin', 'http://127.0.0.1:5173');
    response.setHeader('Access-Control-Allow-Headers', 'content-type');
    if (request.method === 'OPTIONS') { response.end(); return; }
    origins.push(request.headers.origin ?? '');
    if (request.headers.origin !== 'http://127.0.0.1:5173') { response.writeHead(403); response.end(); return; }
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ endpoint: request.url, origin: request.headers.origin, method: request.method }));
  });
  await new Promise<void>((resolve) => runner.listen(0, '127.0.0.1', resolve));
  const runnerUrl = `http://127.0.0.1:${(runner.address() as AddressInfo).port}`;
  try {
    const content = story([{ eventId: 'ev_level', type: 'level', params: { levelId: 'lv_safe', initPayload: JSON.stringify({ runner: runnerUrl }) } }]);
    content.levels = [{ levelId: 'lv_safe', title: '隔离检查', entry: 'levels/isolated.html', supportsSnapshot: true }];
    await page.route('**/__player-audit__/levels/data.json', (route) => route.fulfill({ json: { chapter: '相对路径正常' } }));
    await page.route('**/__player-audit__/levels/isolated.html', (route) => route.fulfill({ contentType: 'text/html', body: `<!doctype html><html><body><pre id="result">等待</pre><script>
      parent.postMessage({type:'ready'},'*');
      addEventListener('message',async event=>{if(event.data?.type!=='init')return;const result={locationOrigin:location.origin,securityOrigin:self.origin};
        try{parent.document.body.dataset.compromised='yes';result.parent='allowed';}catch{result.parent='blocked';}
        try{localStorage.getItem('shiguang-player-save-v1');result.storage='allowed';}catch{result.storage='blocked';}
        const payload=JSON.parse(event.data.payload);try{result.relative=await fetch('data.json').then(r=>r.json());}catch(e){result.relative=String(e);}
        try{result.health=await fetch(payload.runner+'/health').then(r=>r.json());result.run=await fetch(payload.runner+'/run',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}).then(r=>r.json());}catch(e){result.runner=String(e);}
        try{await fetch(payload.runner+'/v1/secret');result.outside='allowed';}catch{result.outside='blocked';}
        result.direct=await new Promise(resolve=>{const request=new XMLHttpRequest();request.onerror=()=>resolve('blocked');request.onload=()=>resolve('allowed');request.open('GET',payload.runner+'/v1/direct');request.send();});
        document.querySelector('#result').textContent=JSON.stringify(result);
      });</script></body></html>` }));
    await mount(page, content);
    await page.getByTestId('start').click();
    const resultNode = page.frameLocator('[data-testid="level-frame"]').locator('#result');
    await expect(resultNode).toContainText('"outside":"blocked"');
    const result = JSON.parse(await resultNode.textContent() ?? '{}');
    expect(result.locationOrigin).toBe(new URL(page.url()).origin);
    expect(result.securityOrigin).toBe('null');
    expect(result.parent).toBe('blocked');
    expect(result.storage).toBe('blocked');
    expect(result.relative).toEqual({ chapter: '相对路径正常' });
    expect(result.health).toMatchObject({ endpoint: '/health', origin: 'http://127.0.0.1:5173' });
    expect(result.run).toMatchObject({ endpoint: '/run', method: 'POST' });
    expect(result.direct).toBe('blocked');
    expect(origins).toEqual(['http://127.0.0.1:5173', 'http://127.0.0.1:5173']);
    expect(paths.some((url) => url.startsWith('/v1/'))).toBe(false);
    expect(await page.locator('body').getAttribute('data-compromised')).toBeNull();
    await expect(page.getByTestId('level-frame')).toHaveAttribute('sandbox', 'allow-scripts');
    await expect(page.getByTestId('level-frame')).toHaveAttribute('src', /^blob:/);
  } finally { await new Promise<void>((resolve) => runner.close(() => resolve())); }
});

test('旧式Origin校验关卡完成ready握手、保存序号1进度点并回传完成', async ({ page }) => {
  const content = story([{ eventId: 'ev_level', type: 'level', params: { levelId: 'lv_legacy' } }, dialogue('ev_after', '旧关卡完成之后')], 'pr_legacy_origin');
  content.levels = [{ levelId: 'lv_legacy', title: '旧式握手关卡', entry: 'levels/legacy.html', supportsSnapshot: true }];
  await page.route('**/__player-audit__/levels/legacy.html', (route) => route.fulfill({ contentType: 'text/html', body: `<!doctype html><html><body><p id="handshake">等待初始化</p><button id="done" disabled>完成</button><script>
    if(location.origin!=='null')parent.postMessage({type:'ready'},location.origin);
    addEventListener('message',event=>{
      if(event.source!==parent||event.origin!==location.origin||event.data?.type!=='init')return;
      document.querySelector('#handshake').textContent='旧式Origin校验通过';
      document.querySelector('#done').disabled=false;
      parent.postMessage({type:'checkpoint',requestId:'legacy-checkpoint',messageSeq:1,payload:{legacy:true}},location.origin);
    });
    document.querySelector('#done').onclick=()=>parent.postMessage({type:'complete',requestId:'legacy-complete',messageSeq:2,payload:{solved:true}},location.origin);
  </script></body></html>` }));
  await mount(page, content, 'legacy-origin');
  await page.getByTestId('start').click();
  const frame = page.frameLocator('[data-testid="level-frame"]');
  await expect(frame.locator('#handshake')).toHaveText('旧式Origin校验通过');
  await expect(page.locator('#player-status')).toContainText('已记录进度点');
  await page.getByTestId('save').click();
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem('shiguang-player-save-v1:pr_legacy_origin')!).levels);
  expect(saved.lastMessageSeq).toBe(1);
  expect(saved.snapshot).toEqual({ legacy: true });
  await frame.locator('#done').click();
  await expect(page.getByTestId('dialogue')).toContainText('旧关卡完成之后');
  await expect(page.getByTestId('errors')).toHaveCount(0);
});

test('缺失或不发送ready的关卡给出错误并继续，避免永久等待', async ({ page }) => {
  await page.clock.install();
  const content = story([{ eventId: 'ev_level', type: 'level', params: { levelId: 'lv_missing' } }, dialogue('ev_after', '错误之后继续')]);
  content.levels.push({ levelId: 'lv_missing', title: '无响应关卡', entry: 'levels/missing.html', supportsSnapshot: true });
  await page.route('**/__player-audit__/levels/missing.html', (route) => route.fulfill({ status: 404, body: 'Missing', contentType: 'text/plain' }));
  await mount(page, content);
  await page.getByTestId('start').click();
  await page.clock.runFor(11000);
  await expect(page.getByTestId('dialogue')).toContainText('错误之后继续');
  await expect(page.getByTestId('errors')).toContainText('LEVEL');
});

test('空槽位和损坏存档读取失败保留当前进度，循环保护提供恢复入口', async ({ page }) => {
  await mount(page, story([dialogue('ev_one', '保留这句'), dialogue('ev_two', '下一句')]));
  await page.getByTestId('start').click();
  await page.getByTestId('load').click();
  await expect(page.locator('#player-status')).toContainText('当前进度已保留');
  await expect(page.getByTestId('dialogue')).toContainText('保留这句');
  await page.evaluate(() => localStorage.setItem('shiguang-player-save-v1:pr_audit', '{broken'));
  await page.getByTestId('load').click();
  await expect(page.locator('#player-status')).toContainText('无法读取存档');
  await expect(page.getByTestId('dialogue')).toContainText('保留这句');
  await page.getByTestId('advance').click();
  await expect(page.getByTestId('dialogue')).toContainText('下一句');
  const loop = story([{ eventId: 'ev_loop', type: 'jump', params: { targetEventId: 'ev_loop' } }]);
  await mount(page, loop, 'loop');
  await page.getByTestId('start').click();
  await expect(page.getByTestId('errors')).toContainText('RUNTIME_LOOP');
  await page.getByTestId('restart').click();
  await expect(page.getByTestId('start')).toBeEnabled();
});
