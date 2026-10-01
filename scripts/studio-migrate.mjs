// 旧 Studio v1 配置迁移：把浏览器体验版的 JSON 工程转换为磁盘工程（经后台 API 制作）。
// 用法：
//   node scripts/studio-migrate.mjs --input <v1.json> [--workspace <dir>] [--title <标题>] [--json <报告>]
//                                   [--dry-run] [--keep-workspace]
// 说明：
//   - v1 格式见 src/studio/model.ts（version/id/title/chapter/entry/assets[]/nodes[]）。
//   - 迁移先预览，再保持入口与 next/choices 图；不支持的字段逐条记录，不静默丢弃。
//   - 成功后始终保留工作区；失败时仅清理脚本自己创建的临时目录（--keep-workspace 可保留）。
//   - 保留原始配置：脚本只读取输入文件，不修改它；同时把原文件复制进工程 imports/ 供追溯。
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync, copyFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { argValue } from './studio-lib.mjs';
import { makeId } from '../server/src/core/ids.ts';
import { planV1Events, migrationPhases } from './studio-v1-map.mjs';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const argv = process.argv.slice(2);
const inputFile = path.resolve(ROOT, argValue(argv, '--input', ''));
const dryRun = argv.includes('--dry-run');
const keepWorkspace = argv.includes('--keep-workspace');
if (!inputFile || !existsSync(inputFile)) {
  console.error('用法：node scripts/studio-migrate.mjs --input <v1.json> [--workspace <dir>] [--dry-run]');
  process.exit(2);
}

const source = JSON.parse(readFileSync(inputFile, 'utf8'));
const report = {
  kind: 'studio-migration-report',
  ranAt: new Date().toISOString(),
  input: path.relative(ROOT, inputFile).replace(/\\/g, '/'),
  dryRun,
  sourceShape: {
    version: source.version ?? null,
    id: source.id ?? null,
    title: source.title ?? null,
    chapter: source.chapter ?? null,
    nodeCount: Array.isArray(source.nodes) ? source.nodes.length : 0,
    assetCount: Array.isArray(source.assets) ? source.assets.length : 0,
  },
  supported: [],
  unsupported: [],
  warnings: [],
  nodeTypeCounts: {},
  embeddedAssets: 0,
  externalAssets: 0,
  createdProjectId: null,
  revision: null,
};

function note(list, message) {
  if (!list.includes(message)) list.push(message);
}

// 1. 结构预检：迁移只接受 v1 工程
if (source.version !== 1) {
  report.unsupported.push(`工程格式版本 ${JSON.stringify(source.version)}：迁移工具只支持 version=1`);
}
if (!Array.isArray(source.nodes) || !source.nodes.length) {
  report.unsupported.push('nodes 缺失或为空：没有可迁移的剧情节点');
}
if (!Array.isArray(source.assets)) report.warnings.push('assets 缺失：将按空素材表迁移');

// 2. 节点 → 事件映射（与方案第 12 节一致）
const nodes = Array.isArray(source.nodes) ? source.nodes : [];
for (const node of nodes) {
  const type = node?.type;
  report.nodeTypeCounts[type] = (report.nodeTypeCounts[type] ?? 0) + 1;
}
for (const asset of Array.isArray(source.assets) ? source.assets : []) {
  if (typeof asset?.src === 'string' && asset.src.startsWith('data:image/')) report.embeddedAssets += 1;
  else report.externalAssets += 1;
}
note(report.supported, '节点类型 dialogue → 设置背景/人物 + 对白事件');
note(report.supported, '节点类型 choice → 选择事件（选项按 next 建立事件跳转）');
note(report.supported, '节点类型 ending → 结束事件');
note(report.supported, '节点类型 level → 关卡事件（需要先安装对应 HTML 关卡程序）');
note(report.supported, '嵌入的 data:image 素材 → assets/originals 文件');
note(report.supported, 'locked 标记 → 记录为迁移警告（v2 没有锁定字段）');
if (nodes.some((node) => node?.level && node.level !== 'constellation')) {
  note(report.unsupported, '非星图关卡：v1 的 level 字段只在体验版支持 constellation');
}
if (nodes.some((node) => node?.locked)) {
  report.warnings.push('部分节点带有 locked 标记：v2 没有该字段，迁移后不保留锁定语义');
}
if (Array.isArray(source.assets) && source.assets.length > 30) {
  report.warnings.push(`素材数量 ${source.assets.length} 超过体验版上限 30：迁移不受该上限限制，但需要确认素材文件仍在`);
}

if (source.version === 1 && nodes.length) {
  try { planV1Events(source, 'sc_preflight', 'preflight'); }
  catch (error) { report.unsupported.push(error.message); }
}

if (report.unsupported.length) {
  console.error('迁移预检未通过：');
  for (const item of report.unsupported) console.error(`  - ${item}`);
  const out = argValue(argv, '--json');
  if (out) writeFileSync(path.resolve(ROOT, out), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  process.exit(3);
}

if (dryRun) {
  console.log('预检通过（dry-run，不写入工程）：');
  console.log(JSON.stringify(report, null, 2));
  const out = argValue(argv, '--json');
  if (out) writeFileSync(path.resolve(ROOT, out), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  process.exit(0);
}

// 3. 实际迁移：启动后台 → 建工程 → 导入素材 → 生成事件 → 校验
const explicitWorkspace = argValue(argv, '--workspace');
const workspace = explicitWorkspace ? path.resolve(explicitWorkspace) : mkdtempSync(path.join(os.tmpdir(), 'studio-migrate-'));
const ownsWorkspace = !explicitWorkspace;
const migrationId = makeId('mig', 12).slice(4);
report.workspace = workspace;
report.migrationId = migrationId;
let backend = null;

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function discoverBackend() {
  const discovery = path.join(workspace, '.studio-service', 'runtime.json');
  if (!existsSync(discovery)) return null;
  try {
    const record = JSON.parse(readFileSync(discovery, 'utf8'));
    const response = await fetch(`${record.url}/v1/health`, { signal: AbortSignal.timeout(1000) });
    const health = response.ok ? await response.json() : null;
    return health?.instanceId === record.instanceId ? record.url : null;
  } catch { return null; }
}

async function startBackend() {
  const existingUrl = await discoverBackend();
  if (existingUrl) return { child: null, url: existingUrl };
  const child = spawn(process.execPath, [path.join(ROOT, 'server', 'src', 'main.ts'), '--workspace', workspace, '--port', '0'], { stdio: 'ignore' });
  // 立即记录进程所有权，启动失败也能清理；不会停止调用方已有后台。
  backend = { child, url: null };
  let spawnError = null;
  child.on('error', (error) => { spawnError = error; });
  for (let attempt = 0; attempt < 200; attempt += 1) {
    await delay(100);
    if (spawnError) throw spawnError;
    if (child.exitCode !== null) throw new Error(`后台启动失败（退出码 ${child.exitCode}）`);
    const url = await discoverBackend();
    if (url) return { child, url };
  }
  throw new Error('后台启动超时');
}

async function stopBackend() {
  if (!backend?.child?.pid || backend.child.exitCode !== null || backend.child.signalCode !== null) return;
  const child = backend.child;
  await new Promise((resolve) => {
    child.once('close', resolve);
    child.kill('SIGKILL');
  });
}

function cleanFailedTemporaryWorkspace() {
  if (!ownsWorkspace || keepWorkspace || !existsSync(workspace)) return;
  // 只清理本次 mkdtemp 创建的目录，绝不递归删除显式工作区。
  if (path.dirname(workspace) !== path.resolve(os.tmpdir()) || !path.basename(workspace).startsWith('studio-migrate-')) {
    throw new Error('临时工作区清理路径越界');
  }
  rmSync(workspace, { recursive: true, force: true });
}

async function main() {
  backend = await startBackend();
  const pairingKey = JSON.parse(readFileSync(path.join(workspace, '.studio-service', 'credentials', 'pairing.key'), 'utf8')).pairingKey;
  const session = await (await fetch(`${backend.url}/v1/sessions`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ pairingKey, clientName: 'migrate', scopes: ['read', 'edit'] }),
  })).json();
  const token = session.token;
  const api = async (method, pathname, body) => {
    const response = await fetch(`${backend.url}${pathname}`, {
      method,
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const text = await response.text();
    const parsed = text ? JSON.parse(text) : {};
    if (!response.ok) throw new Error(`${method} ${pathname} → HTTP ${response.status}: ${JSON.stringify(parsed).slice(0, 300)}`);
    return parsed;
  };

  const title = argValue(argv, '--title', source.title || '迁移工程');
  // --project <id>：导入到已有工程（供编辑器/自动化在既有工程里追加 v1 内容）；
  // 不带该参数时保持原行为：新建工程。两条路径共用同一套节点→事件映射。
  const targetProjectId = argValue(argv, '--project', '');
  let projectId = targetProjectId;
  let created;
  if (targetProjectId) {
    const existing = await api('GET', `/v1/projects/${targetProjectId}/outline`);
    const outline = Array.isArray(existing.outline) ? existing.outline : [];
    const firstChapter = outline[0];
    if (!firstChapter) throw new Error(`工程 ${targetProjectId} 没有章节，无法导入`);
    created = { projectId: targetProjectId, revision: (await api('GET', `/v1/projects/${targetProjectId}`)).revision, sceneId: firstChapter.scenes[0]?.sceneId };
    // 已有工程：把 v1 节点写入一个新场景，避免与现有事件混在一起
    const sceneTitle = argValue(argv, '--scene-title', 'v1 导入');
    const sceneId = `sc_v1import${migrationId}`;
    const sceneTx = await api('POST', `/v1/projects/${targetProjectId}/transactions`, {
      requestId: `migrate-scene-${sceneId}`,
      projectId: targetProjectId,
      baseRevision: created.revision,
      operations: [{ action: 'scene.create', payload: { chapterId: firstChapter.chapterId, title: sceneTitle, sceneId } }],
    });
    if (sceneTx.status !== 'applied') throw new Error(`创建导入场景失败：${JSON.stringify(sceneTx.issues?.slice(0, 3))}`);
    created = { ...created, revision: sceneTx.revision, sceneId };
    report.importedIntoExistingProject = true;
  } else {
    created = await api('POST', '/v1/projects', { title, chapterTitle: source.chapter || '第一章', sceneTitle: '迁移场景' });
  }
  report.createdProjectId = created.projectId;
  let revision = created.revision;
  const sceneId = created.sceneId;

  // 保留原始配置：复制进工程 imports/ 供追溯
  mkdirSync(path.join(workspace, 'imports'), { recursive: true });
  const preserved = path.join(workspace, 'imports', `v1-source-${migrationId}-${path.basename(inputFile)}`);
  copyFileSync(inputFile, preserved);
  report.sourcePreservedAt = path.relative(workspace, preserved).replace(/\\/g, '/');

  // 素材：嵌入图片写成文件后登记；外部路径素材记为未迁移（v2 不引用工程外路径）
  const assetIdByOldId = new Map();
  const assetDir = path.join(workspace, 'imports', 'migrated');
  mkdirSync(assetDir, { recursive: true });
  let assetIndex = 0;
  for (const asset of Array.isArray(source.assets) ? source.assets : []) {
    if (typeof asset?.src === 'string' && asset.src.startsWith('data:image/')) {
      const match = /^data:image\/(png|jpeg|webp);base64,(.+)$/.exec(asset.src);
      if (!match) { report.warnings.push(`素材 ${asset.id} 的内嵌数据无法解析，已跳过`); continue; }
      const ext = match[1] === 'jpeg' ? '.jpg' : `.${match[1]}`;
      const file = path.join(assetDir, `${migrationId}-${assetIndex}${ext}`);
      writeFileSync(file, Buffer.from(match[2], 'base64'));
      const assetId = `as_mig${migrationId}${assetIndex.toString(36)}`;
      assetIndex += 1;
      const tx = await api('POST', `/v1/projects/${created.projectId}/transactions`, {
        requestId: `migrate-${migrationId}-asset-${assetIndex}`,
        projectId: created.projectId,
        baseRevision: revision,
        operations: [{ action: 'asset.registerFromFile', payload: { sourcePath: file, name: asset.name || asset.id, kind: asset.kind === 'character' ? 'character' : 'background', assetId } }],
      });
      if (tx.status !== 'applied') throw new Error(`素材迁移被拒绝：${JSON.stringify(tx.issues?.slice(0, 3))}`);
      revision = tx.revision;
      assetIdByOldId.set(asset.id, assetId);
      note(report.supported, `素材 ${asset.id}（${asset.kind}）→ ${assetId}`);
    } else {
      report.warnings.push(`素材 ${asset?.id} 引用工程内路径 ${asset?.src}：v2 使用 assets/originals，请用 studio_import_asset 重新导入`);
    }
  }

  const plan = planV1Events(source, sceneId, migrationId, assetIdByOldId);
  report.warnings.push(...plan.warnings);
  report.nodeEventIds = plan.nodeEventIds;
  report.assetIds = Object.fromEntries(assetIdByOldId);
  let applied = 0;
  for (const [phase, operations] of migrationPhases(plan.operations).entries()) {
    for (let offset = 0; offset < operations.length; offset += 200) {
      const batch = operations.slice(offset, offset + 200);
      const tx = await api('POST', `/v1/projects/${created.projectId}/transactions`, {
        requestId: `migrate-${migrationId}-events-${phase}-${offset}`,
        projectId: created.projectId,
        baseRevision: revision,
        operations: batch,
        summary: `迁移 v1 节点（阶段 ${phase + 1}，第 ${offset + 1}–${offset + batch.length} 个操作）`,
      });
      if (tx.status !== 'applied') throw new Error(`迁移事务被拒绝：${JSON.stringify(tx.issues?.slice(0, 3))}`);
      revision = tx.revision;
      applied += batch.length;
    }
  }

  const validation = await api('GET', `/v1/projects/${created.projectId}/validate`);
  report.revision = revision;
  report.appliedOperations = applied;
  report.validation = { ok: validation.ok, issueCount: validation.issues?.length ?? 0 };

  const out = argValue(argv, '--json');
  if (out) writeFileSync(path.resolve(ROOT, out), `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  console.log(`迁移完成：工程 ${report.createdProjectId}，修订 ${revision}，操作 ${applied} 个，校验 ${validation.ok ? '通过' : '有问题'}`);
  console.log(`工作区：${workspace}（已保留）`);
  for (const warning of report.warnings) console.log(`  警告：${warning}`);

  await stopBackend();
  if (!validation.ok) process.exitCode = 4;
}

main().catch(async (error) => {
  console.error(`迁移失败：${error.message}`);
  await stopBackend();
  cleanFailedTemporaryWorkspace();
  process.exit(1);
});
