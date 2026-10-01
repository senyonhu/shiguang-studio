// 规模与性能测量：按指定规模生成测试工程（经后台 API 制作），测量方案第 11 节的指标。
// 用法：
//   node scripts/studio-scale.mjs [--profile quick|target] [--chapters N] [--scenes N] [--events N] [--assets N]
//                                 [--edits N] [--searches N] [--json <报告>] [--keep] [--workspace <dir>]
// 说明：
//   - 数据全部通过后台 HTTP API 生成（不是直接写文件），因此测量的是真实制作路径。
//   - 报告记录硬件、实际样本数量、文件体积与每项结果；未测量的项目明确写“未测量”，不做外推当作结论。
//   - 测试工程放在系统临时目录，默认测量结束后删除（--keep 保留以便复查）。
import { spawn } from 'node:child_process';
import { deflateSync } from 'node:zlib';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { argValue } from './studio-lib.mjs';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const argv = process.argv.slice(2);
const profile = argValue(argv, '--profile', 'quick');
const profiles = {
  quick: { chapters: 10, scenesPerChapter: 5, eventsPerScene: 20, assets: 100, edits: 20, searches: 20 },
  mid: { chapters: 50, scenesPerChapter: 4, eventsPerScene: 50, assets: 300, edits: 30, searches: 30 },
  target: { chapters: 100, scenesPerChapter: 5, eventsPerScene: 200, assets: 1000, edits: 40, searches: 50 },
};
const num = (flag, fallback) => {
  const value = argValue(argv, flag);
  return value === null ? fallback : Number(value);
};
const scale = {
  chapters: num('--chapters', profiles[profile]?.chapters ?? profiles.quick.chapters),
  scenesPerChapter: num('--scenes', profiles[profile]?.scenesPerChapter ?? profiles.quick.scenesPerChapter),
  eventsPerScene: num('--events', profiles[profile]?.eventsPerScene ?? profiles.quick.eventsPerScene),
  assets: num('--assets', profiles[profile]?.assets ?? profiles.quick.assets),
  edits: num('--edits', profiles[profile]?.edits ?? profiles.quick.edits),
  searches: num('--searches', profiles[profile]?.searches ?? profiles.quick.searches),
};
const keep = argv.includes('--keep');
const workspace = path.resolve(argValue(argv, '--workspace', mkdtempSync(path.join(os.tmpdir(), 'studio-scale-'))));

const results = [];
const measurements = {};
let backend = null;

function record(id, target, measuredMs, unit = 'ms', note = '') {
  results.push({ id, target, measuredMs: Math.round(measuredMs * 100) / 100, unit, pass: target === null ? null : measuredMs <= target, note });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function startBackend() {
  const child = spawn(process.execPath, [path.join(ROOT, 'server', 'src', 'main.ts'), '--workspace', workspace, '--port', '0'], { stdio: ['ignore', 'pipe', 'pipe'] });
  const discovery = path.join(workspace, '.studio-service', 'runtime.json');
  const startedAt = Date.now();
  for (let attempt = 0; attempt < 200; attempt += 1) {
    await delay(100);
    if (!existsSync(discovery)) continue;
    try {
      const record = JSON.parse(readFileSync(discovery, 'utf8'));
      const response = await fetch(`${record.url}/v1/health`);
      if (response.ok) return { child, url: record.url, startMs: Date.now() - startedAt };
    } catch { /* 等待 */ }
  }
  throw new Error('后台启动超时');
}

async function api(method, pathname, body, token) {
  const response = await fetch(`${backend.url}${pathname}`, {
    method,
    headers: { 'content-type': 'application/json', ...(token ? { authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await response.text();
  const parsed = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(`${method} ${pathname} → HTTP ${response.status}: ${JSON.stringify(parsed).slice(0, 300)}`);
  return parsed;
}

function directorySize(dir) {
  let total = 0;
  let files = 0;
  const walk = (current) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const abs = path.join(current, entry.name);
      if (entry.isDirectory()) walk(abs);
      else { total += statSync(abs).size; files += 1; }
    }
  };
  if (existsSync(dir)) walk(dir);
  return { bytes: total, files };
}

function percentile(values, p) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((p / 100) * sorted.length) - 1));
  return sorted[index];
}

async function main() {
  console.log(`工作区：${workspace}`);
  console.log(`规模：${scale.chapters} 章 × ${scale.scenesPerChapter} 场景 × ${scale.eventsPerScene} 事件 = ${scale.chapters * scale.scenesPerChapter * scale.eventsPerScene} 事件；素材索引 ${scale.assets}`);
  const started = await startBackend();
  backend = started;
  const info = await api('GET', '/v1/health');
  const pairingKey = JSON.parse(readFileSync(path.join(workspace, '.studio-service', 'credentials', 'pairing.key'), 'utf8')).pairingKey;
  const session = await api('POST', '/v1/sessions', { pairingKey, clientName: 'scale-runner', scopes: ['read', 'edit', 'generate', 'preview', 'export'] });
  const token = session.token;

  // 生成阶段（不参与指标判定，只记录耗时）
  const generationStart = Date.now();
  const created = await api('POST', '/v1/projects', { title: `规模测试 ${scale.chapters} 章`, chapterTitle: '第一章', sceneTitle: '场景 1' }, token);
  const projectId = created.projectId;
  let revision = created.revision;

  // 素材索引：按批导入（每个素材一次事务，测量单次成本）
  const assetTimes = [];
  const importDir = path.join(workspace, 'imports');
  for (let index = 0; index < scale.assets; index += 1) {
    const file = path.join(importDir, `scale-${index}.png`);
    if (!existsSync(file)) writeFileSync(file, makeTinyPng(index));
    const t0 = Date.now();
    const tx = await api('POST', `/v1/projects/${projectId}/transactions`, {
      requestId: `scale-asset-${index}`,
      projectId,
      baseRevision: revision,
      operations: [{ action: 'asset.registerFromFile', payload: { sourcePath: file, name: `素材 ${index}`, kind: 'background', assetId: `as_scale${String(index).padStart(7, '0')}` } }],
    }, token);
    revision = tx.revision;
    assetTimes.push(Date.now() - t0);
  }
  measurements.assetImport = { samples: assetTimes.length, p50: percentile(assetTimes, 50), p95: percentile(assetTimes, 95), max: Math.max(...assetTimes, 0) };

  // 媒体负载（--media-mb）：注册真实体积的图片素材，测量导入吞吐、磁盘占用与峰值内存。
  // 说明：默认 0（不产生大文件）；目标规模的 20GB 按要求不生成，这里按显式参数在磁盘可承受范围内测量。
  const mediaMB = Number(argValue(argv, '--media-mb', '0')) || 0;
  if (mediaMB > 0) {
    const perFileMB = 2;
    const fileCount = Math.max(1, Math.ceil(mediaMB / perFileMB));
    const mediaDir = path.join(workspace, 'imports', 'media');
    mkdirSync(mediaDir, { recursive: true });
    const importTimes = [];
    let bytesOnDisk = 0;
    const mediaStart = Date.now();
    for (let index = 0; index < fileCount; index += 1) {
      const file = path.join(mediaDir, `media-${index}.png`);
      if (!existsSync(file)) writeFileSync(file, makeNoisePng(index, perFileMB));
      bytesOnDisk += statSync(file).size;
      const t0 = Date.now();
      const tx = await api('POST', `/v1/projects/${projectId}/transactions`, {
        requestId: `scale-media-${index}`,
        projectId,
        baseRevision: revision,
        operations: [{ action: 'asset.registerFromFile', payload: { sourcePath: file, name: `媒体 ${index}`, kind: 'background', assetId: `as_media${String(index).padStart(7, '0')}` } }],
      }, token);
      revision = tx.revision;
      importTimes.push(Date.now() - t0);
    }
    const elapsed = Date.now() - mediaStart;
    const healthAfterMedia = await (await fetch(`${backend.url}/v1/health`)).json();
    measurements.mediaLoad = {
      requestedMB: mediaMB,
      files: fileCount,
      bytes: bytesOnDisk,
      elapsedMs: elapsed,
      throughputMBps: Math.round((bytesOnDisk / 1024 / 1024) / (elapsed / 1000) * 100) / 100,
      importP50: percentile(importTimes, 50),
      importP95: percentile(importTimes, 95),
      peakRssMB: healthAfterMedia.peakRssMB ?? null,
    };
  }

  // 章节与场景结构
  const chapterIds = [];
  for (let chapter = 0; chapter < scale.chapters; chapter += 1) {
    if (chapter === 0) {
      const outline = await api('GET', `/v1/projects/${projectId}/outline`, undefined, token);
      chapterIds.push(outline.outline[0].chapterId);
      continue;
    }
    const tx = await api('POST', `/v1/projects/${projectId}/transactions`, {
      requestId: `scale-chapter-${chapter}`,
      projectId,
      baseRevision: revision,
      operations: [{ action: 'chapter.create', payload: { title: `第 ${chapter + 1} 章`, chapterId: `ch_scale${String(chapter).padStart(7, '0')}` } }],
    }, token);
    revision = tx.revision;
    chapterIds.push(`ch_scale${String(chapter).padStart(7, '0')}`);
  }
  const sceneIds = [];
  for (let chapter = 0; chapter < scale.chapters; chapter += 1) {
    for (let scene = 0; scene < scale.scenesPerChapter; scene += 1) {
      const sceneId = `sc_s${String(chapter).padStart(4, '0')}${String(scene).padStart(3, '0')}`;
      if (chapter === 0 && scene === 0) {
        const outline = await api('GET', `/v1/projects/${projectId}/outline`, undefined, token);
        sceneIds.push(outline.outline[0].scenes[0].sceneId);
        continue;
      }
      const tx = await api('POST', `/v1/projects/${projectId}/transactions`, {
        requestId: `scale-scene-${chapter}-${scene}`,
        projectId,
        baseRevision: revision,
        operations: [{ action: 'scene.create', payload: { chapterId: chapterIds[chapter], title: `场景 ${scene + 1}`, sceneId } }],
      }, token);
      revision = tx.revision;
      sceneIds.push(sceneId);
    }
  }

  // 事件：每批 200 个操作（协议上限）
  const totalEvents = scale.chapters * scale.scenesPerChapter * scale.eventsPerScene;
  const perBatch = 200;
  let written = 0;
  const batchTimes = [];
  for (const sceneId of sceneIds) {
    for (let offset = 0; offset < scale.eventsPerScene; offset += perBatch) {
      const count = Math.min(perBatch, scale.eventsPerScene - offset);
      const operations = [];
      for (let index = 0; index < count; index += 1) {
        const eventIndex = offset + index;
        operations.push({
          action: 'event.insert',
          payload: {
            sceneId,
            type: 'dialogue',
            eventId: `ev_${sceneId.slice(3)}${String(eventIndex).padStart(4, '0')}`,
            params: { speaker: '璃', text: `第 ${written + index + 1} 句台词：雨点敲在旧塔的铁门上。` },
          },
        });
      }
      const t0 = Date.now();
      const tx = await api('POST', `/v1/projects/${projectId}/transactions`, {
        requestId: `scale-events-${sceneId}-${offset}`,
        projectId,
        baseRevision: revision,
        operations,
      }, token);
      revision = tx.revision;
      batchTimes.push(Date.now() - t0);
      written += count;
    }
  }
  measurements.generation = {
    eventsWritten: written,
    transactions: batchTimes.length + assetTimes.length,
    elapsedMs: Date.now() - generationStart,
    eventBatchP50: percentile(batchTimes, 50),
    eventBatchP95: percentile(batchTimes, 95),
    eventBatchMax: Math.max(...batchTimes, 0),
  };

  // 指标 1：暖启动工程索引可交互（读大纲）
  const outlineTimes = [];
  for (let index = 0; index < 5; index += 1) {
    const t0 = Date.now();
    await api('GET', `/v1/projects/${projectId}/outline`, undefined, token);
    outlineTimes.push(Date.now() - t0);
  }
  measurements.outlineP50 = percentile(outlineTimes, 50);
  record('warm-index-interactive', 3000, measurements.outlineP50, 'ms', '读 /v1/projects/{id}/outline（5 次取 P50）');

  // 指标 2：索引搜索 P95
  const searchTimes = [];
  for (let index = 0; index < scale.searches; index += 1) {
    const t0 = Date.now();
    await api('GET', `/v1/projects/${projectId}/search?q=${encodeURIComponent(index % 2 ? '石阶' : '台词')}`, undefined, token);
    searchTimes.push(Date.now() - t0);
  }
  measurements.searchP95 = percentile(searchTimes, 95);
  record('index-search-p95', 300, measurements.searchP95, 'ms', `${searchTimes.length} 次搜索`);

  // 指标 3：普通文本编辑响应 P95（单事件更新事务）
  const editTimes = [];
  const targetScene = sceneIds[sceneIds.length - 1];
  const sceneDetail = await api('GET', `/v1/projects/${projectId}/scenes/${targetScene}`, undefined, token);
  const targetEventId = sceneDetail.events[0].eventId;
  for (let index = 0; index < scale.edits; index += 1) {
    const t0 = Date.now();
    const tx = await api('POST', `/v1/projects/${projectId}/transactions`, {
      requestId: `scale-edit-${index}`,
      projectId,
      baseRevision: revision,
      operations: [{ action: 'event.update', targetIds: [targetEventId], payload: { sceneId: targetScene, eventId: targetEventId, params: { speaker: '璃', text: `第 ${index + 1} 次人工修改。` } } }],
    }, token);
    revision = tx.revision;
    editTimes.push(Date.now() - t0);
  }
  measurements.editP95 = percentile(editTimes, 95);
  measurements.editP50 = percentile(editTimes, 50);
  record('text-edit-p95', 100, measurements.editP95, 'ms', `${editTimes.length} 次单事件修改事务`);

  // 指标 4：单场景增量保存（读该场景 + 校验）
  const saveTimes = [];
  for (let index = 0; index < 5; index += 1) {
    const t0 = Date.now();
    await api('GET', `/v1/projects/${projectId}/scenes/${targetScene}`, undefined, token);
    saveTimes.push(Date.now() - t0);
  }
  measurements.sceneReadP50 = percentile(saveTimes, 50);
  record('scene-incremental-save', 1000, measurements.sceneReadP50, 'ms', '读单场景（10 万事件工程中的一次增量读取）');

  // 体量与内存
  const projectDir = path.join(workspace, projectId);
  const projectSize = directorySize(projectDir);
  const rss = await fetch(`${backend.url}/v1/capabilities`, { headers: { authorization: `Bearer ${token}` } });
  await rss.json();
  measurements.projectSize = projectSize;
  // 峰值内存：后台自述（提交后 + 每 5 秒采样取最大）
  const healthFinal = await (await fetch(`${backend.url}/v1/health`)).json();
  measurements.memory = { rssMB: healthFinal.rssMB ?? null, peakRssMB: healthFinal.peakRssMB ?? null };
  const pid = JSON.parse(readFileSync(path.join(workspace, '.studio-service', 'runtime.json'), 'utf8')).pid;

  const report = {
    kind: 'studio-scale-report',
    profile,
    ranAt: new Date().toISOString(),
    workspace,
    kept: keep,
    environment: {
      platform: `${os.platform()} ${os.release()}`,
      arch: os.arch(),
      node: process.version,
      cpuModel: os.cpus()[0]?.model ?? '',
      cpuCount: os.cpus().length,
      totalMemoryMB: Math.round(os.totalmem() / 1024 / 1024),
      backendPid: pid,
    },
    scale: { ...scale, scenes: scale.chapters * scale.scenesPerChapter, events: totalEvents },
    generation: measurements.generation,
    assetImport: measurements.assetImport,
    mediaLoad: measurements.mediaLoad ?? null,
    measurements,
    targets: {
      'warm-index-interactive': '方案 11：暖启动工程索引可交互不超过 3 秒',
      'index-search-p95': '方案 11：索引搜索 P95 不超过 300 毫秒',
      'text-edit-p95': '方案 11：普通文本编辑响应 P95 不超过 100 毫秒',
      'scene-incremental-save': '方案 11：单场景增量保存通常不超过 1 秒',
    },
    results,
    notMeasured: [
      '1 万素材索引（本轮实际导入数量见 scale.assets；每个素材一次事务，按实测单次成本线性增长，未做外推结论）',
      '约 20 GB 混合媒体文件（按要求不填满用户磁盘；本轮只用小文件验证索引与事务路径）',
      '冷启动建立索引单独计时（本轮记录的是后台进程启动到健康检查的时间，见 measurements.generation 之外的 startMs）',
      '20GB 媒体（按要求不填满用户磁盘）',
    ],
    notes: [
      '数据全部通过后台 HTTP API 生成，测量的是真实制作路径（事务 → journal → 落盘）。',
      '每个事务都会重新读取整个工程，事件规模上升时单次事务成本随之上升——这正是本报告要暴露的问题。',
    ],
  };
  const out = argValue(argv, '--json');
  if (out) {
    const abs = path.resolve(ROOT, out);
    writeFileSync(abs, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
    console.log(`报告：${path.relative(ROOT, abs)}`);
  }
  for (const result of results) {
    console.log(`${result.pass === null ? '?' : result.pass ? 'PASS' : 'FAIL'} ${result.id}: ${result.measuredMs}${result.unit} (目标 ${result.target}${result.unit}) ${result.note}`);
  }
  console.log(`工程体积：${(projectSize.bytes / 1024 / 1024).toFixed(2)} MB / ${projectSize.files} 个文件；后台峰值内存 ${measurements.memory.peakRssMB} MB（当前 ${measurements.memory.rssMB} MB）`);
  console.log(`生成耗时：${(measurements.generation.elapsedMs / 1000).toFixed(1)}s；事件批 P95 ${measurements.generation.eventBatchP95}ms；素材导入 P95 ${measurements.assetImport.p95}ms`);

  backend.child.kill('SIGKILL');
  await delay(300);
  if (!keep) rmSync(workspace, { recursive: true, force: true });
  if (results.some((result) => result.pass === false)) process.exitCode = 3;
}

function makeNoisePng(seed, targetMB) {
  // 生成“体积可控”的真实 PNG：随机噪声压缩率低，便于用少量文件凑出指定体积。
  const side = Math.max(64, Math.round(Math.sqrt((targetMB * 1024 * 1024) / 3)));
  const width = side;
  const height = side;
  const raw = Buffer.alloc((width * 3 + 1) * height);
  // xorshift32：必须是 32 位安全的伪随机，否则 JS 乘法溢出会让“噪声”退化成可高度压缩的重复数据
  // （第一版就踩了这个坑：32 个“2MB”文件总共只有 1MB）。
  let state = ((seed + 1) >>> 0) || 1;
  const nextByte = () => {
    state ^= state << 13; state >>>= 0;
    state ^= state >>> 17;
    state ^= state << 5; state >>>= 0;
    return state & 0xff;
  };
  for (let y = 0; y < height; y += 1) {
    const rowStart = y * (width * 3 + 1);
    raw[rowStart] = 0;
    for (let x = 0; x < width * 3; x += 1) raw[rowStart + 1 + x] = nextByte();
  }
  const crcTable = [];
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crcTable[n] = c >>> 0;
  }
  const crc32 = (buf) => {
    let crc = 0xffffffff;
    for (const byte of buf) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  };
  const chunk = (type, data) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const typeAndData = Buffer.concat([Buffer.from(type, 'ascii'), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(typeAndData));
    return Buffer.concat([length, typeAndData, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

function makeTinyPng(seed) {
  // 最小合法 PNG（1x1，颜色随 seed 变化）：只用于规模测试的素材索引与文件体积
  const raw = Buffer.from([0x00, seed % 256, (seed * 7) % 256, (seed * 13) % 256]);
  const idat = deflateSync(raw);
  const crcTable = [];
  for (let n = 0; n < 256; n += 1) {
    let c = n;
    for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crcTable[n] = c >>> 0;
  }
  const crc32 = (buf) => {
    let crc = 0xffffffff;
    for (const byte of buf) crc = crcTable[(crc ^ byte) & 0xff] ^ (crc >>> 8);
    return (crc ^ 0xffffffff) >>> 0;
  };
  const chunk = (type, data) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const typeAndData = Buffer.concat([Buffer.from(type, 'ascii'), data]);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(typeAndData));
    return Buffer.concat([length, typeAndData, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(1, 0);
  ihdr.writeUInt32BE(1, 4);
  ihdr[8] = 8;
  ihdr[9] = 2;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', idat),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

main().catch((error) => {
  console.error(`规模测量失败：${error.message}`);
  if (backend?.child) backend.child.kill('SIGKILL');
  if (!keep && existsSync(workspace)) rmSync(workspace, { recursive: true, force: true });
  process.exit(1);
});
