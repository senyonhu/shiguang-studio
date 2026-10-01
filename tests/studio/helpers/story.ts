// 预览与展示器测试共用的样板剧情构建器（只通过后台 HTTP API 制作，模拟 AI 工具的制作流程）。
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { encodeSolidPng } from '../../../server/src/media/decode.ts';

export const LEVEL_HTML = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8" /><title>回声关卡</title></head>
<body>
  <p id="echo">关卡等待宿主初始化</p>
  <script>
    const send = (type, payload, requestId, messageSeq) => parent.postMessage({ type, payload, requestId, messageSeq }, '*');
    parent.postMessage({ type: 'ready', payload: {} }, '*');
    window.addEventListener('message', (event) => {
      const data = event.data || {};
      if (data.type !== 'init') return;
      // 关卡中途存档恢复（KI-017）：宿主在 init 里带上快照时，直接从快照继续而不是重新开始。
      if (data.snapshot) {
        document.getElementById('echo').textContent = '已从快照恢复：angle=' + (data.snapshot.angle ?? '?');
        document.body.dataset.restored = 'true';
        setTimeout(() => { send('complete', { solved: true, resumed: true }, 'done-restored', 1); }, 100);
        return;
      }
      document.getElementById('echo').textContent = '已收到宿主初始化';
      // 真实关卡不会瞬间结束：先记录进度点，再在一段时间后完成（这个间隔正是“关卡中途存档”的窗口）。
      setTimeout(() => { send('checkpoint', { angle: 42 }, 'cp-1', 1); }, 300);
      setTimeout(() => {
        send('complete', { solved: true }, 'done-1', 2);
        // 迟到/重复的完成消息：不应重复推进或重复奖励
        send('complete', { solved: true }, 'done-1', 2);
      }, 1500);
    });
  </script>
</body></html>
`;

/**
 * 第二个关卡程序（KI-020）：机制与回声关卡完全不同——玩家要点亮 3 盏灯。
 * 每点亮一盏发一次 checkpoint，第三盏点亮后发 complete；收到 snapshot 时从已点亮的数量继续。
 */
export const LANTERN_LEVEL_HTML = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8" /><title>灯芯关卡</title></head>
<body>
  <p id="hint">点亮三盏灯</p>
  <div id="lanterns">
    <button class="lantern" id="lantern-1" data-lit="false">灯 1</button>
    <button class="lantern" id="lantern-2" data-lit="false">灯 2</button>
    <button class="lantern" id="lantern-3" data-lit="false">灯 3</button>
  </div>
  <p id="progress">已点亮 0 / 3</p>
  <script>
    var seq = 0;
    var lit = 0;
    var send = function (type, payload) {
      seq += 1;
      parent.postMessage({ type: type, payload: payload, requestId: type + '-' + seq, messageSeq: seq }, '*');
    };
    var paint = function () {
      var count = 0;
      for (var i = 1; i <= 3; i += 1) {
        var node = document.getElementById('lantern-' + i);
        var on = i <= lit;
        node.dataset.lit = on ? 'true' : 'false';
        node.textContent = on ? '灯 ' + i + '（已亮）' : '灯 ' + i;
        if (on) count += 1;
      }
      document.getElementById('progress').textContent = '已点亮 ' + count + ' / 3';
    };
    var light = function (index) {
      if (index !== lit + 1) return; // 必须按顺序点亮
      lit = index;
      paint();
      if (lit === 3) send('complete', { lanterns: lit });
      else send('checkpoint', { lanterns: lit });
    };
    for (var i = 1; i <= 3; i += 1) {
      (function (index) {
        document.getElementById('lantern-' + index).addEventListener('click', function () { light(index); });
      })(i);
    }
    parent.postMessage({ type: 'ready', payload: {} }, '*');
    window.addEventListener('message', function (event) {
      var data = event.data || {};
      if (data.type !== 'init') return;
      // 关卡中途存档恢复：宿主在 init 里带 snapshot 时，从已点亮的数量继续
      lit = data.snapshot && typeof data.snapshot.lanterns === 'number' ? data.snapshot.lanterns : 0;
      paint();
      document.getElementById('hint').textContent = data.snapshot ? '已从快照恢复' : '点亮三盏灯';
      document.body.dataset.restored = data.snapshot ? 'true' : 'false';
    });
  </script>
</body></html>
`;

export interface ApiResponse { status: number; body: Record<string, unknown> }

export async function call(baseUrl: string, token: string, method: 'GET' | 'POST', pathname: string, body?: unknown): Promise<ApiResponse> {
  const response = await fetch(`${baseUrl}${pathname}`, {
    method,
    headers: { authorization: `Bearer ${token}`, 'content-type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  return { status: response.status, body: text ? JSON.parse(text) as Record<string, unknown> : {} };
}

async function transaction(baseUrl: string, token: string, projectId: string, requestId: string, baseRevision: string, operations: unknown[], summary: string): Promise<Record<string, unknown>> {
  const response = await call(baseUrl, token, 'POST', `/v1/projects/${projectId}/transactions`, {
    apiVersion: '1',
    requestId,
    projectId,
    baseRevision,
    operations,
    summary,
  });
  const body = response.body;
  if (body.status !== 'applied') {
    throw new Error(`样板剧情构建失败（${requestId}）：${JSON.stringify(body)}`);
  }
  return body;
}

export interface StoryFixture {
  projectId: string;
  entrySceneId: string;
  levelSceneId: string;
  assetId: string;
  levelId: string;
  revision: string;
  dialogueText: string;
}

/**
 * 构建一段可完整运行的两场景剧情：
 * 场景 1：背景 → 对白 → 选择（进塔）
 * 场景 2：对白 → HTML 关卡（含重复完成消息） → 结局
 */
export async function buildStory(baseUrl: string, token: string, workspaceRoot: string): Promise<StoryFixture> {
  const dialogueText = '雨点敲在旧塔的铁门上。';
  const created = await call(baseUrl, token, 'POST', '/v1/projects', { title: '雨夜旧塔（展示器样板）', chapterTitle: '第一章 塔前', sceneTitle: '雨夜' });
  const projectId = created.body.projectId as string;
  const entrySceneId = created.body.sceneId as string;
  const chapterId = created.body.chapterId as string;
  let revision = created.body.revision as string;

  // 1) 背景素材（写入后台已授权导入目录后经 asset.registerFromFile 登记）
  const importDir = path.join(workspaceRoot, 'imports');
  const pngPath = path.join(importDir, 'rain-night.png');
  writeFileSync(pngPath, encodeSolidPng(12, 8, [22, 30, 44]));
  const assetTx = await transaction(baseUrl, token, projectId, 'p2-asset', revision, [
    { action: 'asset.registerFromFile', payload: { sourcePath: pngPath, name: '雨夜塔门', kind: 'background', assetId: 'as_p2bg000001' } },
  ], '导入背景素材');
  revision = assetTx.revision as string;

  // 2) 关卡定义与程序
  const levelTx = await transaction(baseUrl, token, projectId, 'p2-level-register', revision, [
    { action: 'level.register', payload: { levelId: 'lv_p2echo0001', title: '回声关卡', entry: 'levels/lv_p2echo0001.html', supportsSnapshot: true } },
  ], '登记关卡');
  revision = levelTx.revision as string;
  const levelPath = path.join(importDir, 'echo.html');
  writeFileSync(levelPath, LEVEL_HTML, 'utf8');
  const installTx = await transaction(baseUrl, token, projectId, 'p2-level-install', revision, [
    { action: 'level.installProgram', targetIds: ['lv_p2echo0001'], payload: { levelId: 'lv_p2echo0001', sourcePath: levelPath } },
  ], '安装关卡程序');
  revision = installTx.revision as string;

  // 3) 第二个场景（关卡与结局所在）
  const sceneTx = await transaction(baseUrl, token, projectId, 'p2-scene2', revision, [
    { action: 'scene.create', payload: { chapterId, title: '塔内', sceneId: 'sc_p2inside01' } },
  ], '创建塔内场景');
  revision = sceneTx.revision as string;
  const levelSceneId = 'sc_p2inside01';

  // 4) 场景 1：背景 → 对白 → 选择
  const entryTx = await transaction(baseUrl, token, projectId, 'p2-entry-events', revision, [
    { action: 'event.insert', payload: { sceneId: entrySceneId, type: 'background', params: { assetId: 'as_p2bg000001' }, eventId: 'ev_p2bg000001' } },
    { action: 'event.insert', payload: { sceneId: entrySceneId, type: 'dialogue', params: { speaker: '璃', text: dialogueText }, eventId: 'ev_p2line00001' } },
    { action: 'event.insert', payload: { sceneId: entrySceneId, type: 'choice', params: { prompt: '要进塔吗？', options: [{ label: '进塔', targetSceneId: levelSceneId }] }, eventId: 'ev_p2choice001' } },
  ], '写入开场事件');
  revision = entryTx.revision as string;

  // 5) 场景 2：对白 → 关卡 → 结局
  const insideTx = await transaction(baseUrl, token, projectId, 'p2-inside-events', revision, [
    { action: 'event.insert', payload: { sceneId: levelSceneId, type: 'dialogue', params: { speaker: '璃', text: '门后是向上的石阶。' }, eventId: 'ev_p2line00002' } },
    { action: 'event.insert', payload: { sceneId: levelSceneId, type: 'level', params: { levelId: 'lv_p2echo0001', supportsSnapshot: true }, eventId: 'ev_p2level0001' } },
    { action: 'event.insert', payload: { sceneId: levelSceneId, type: 'ending', params: { endingId: 'end_tower', title: '塔顶' }, eventId: 'ev_p2ending001' } },
  ], '写入塔内事件');
  revision = insideTx.revision as string;

  const validation = await call(baseUrl, token, 'GET', `/v1/projects/${projectId}/validate`);
  if ((validation.body as { ok?: boolean }).ok !== true) {
    throw new Error(`样板剧情未通过校验：${JSON.stringify(validation.body)}`);
  }

  return { projectId, entrySceneId, levelSceneId, assetId: 'as_p2bg000001', levelId: 'lv_p2echo0001', revision, dialogueText };
}
