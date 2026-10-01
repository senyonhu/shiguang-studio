// P5 集成测试：发布包验收检查（完整性/素材齐全/敏感信息/冒烟）与旧 Studio v1 配置迁移。
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync, rmSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { api, makeWorkspace, pairSession, readPairingKey, startBackend, trace, REPO_ROOT } from '../helpers/harness.ts';
import type { ServiceHandle } from '../helpers/harness.ts';
import { buildStory } from '../helpers/story.ts';
import type { StoryFixture } from '../helpers/story.ts';
import { encodeSolidPng } from '../../../server/src/media/decode.ts';

const sha256 = (buffer: Buffer) => createHash('sha256').update(buffer).digest('hex');

function run(command: string, args: string[], options: { cwd?: string } = {}): Promise<{ code: number | null; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn(command, args, { cwd: options.cwd ?? REPO_ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString(); });
    child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });
    child.on('close', (code) => resolve({ code, stdout, stderr }));
  });
}

let workspace = '';
let service: ServiceHandle;
let token = '';
let story: StoryFixture;
let packageDir = '';

beforeAll(async () => {
  workspace = makeWorkspace('p5-release');
  service = await startBackend(workspace, { name: 'p5-release' });
  const session = await pairSession(service.url, readPairingKey(workspace), 'p5-release-cli');
  token = session.token;
  story = await buildStory(service.url, token, workspace);
  const exported = await api(service.url, token, 'POST', `/v1/projects/${story.projectId}/exports`, {});
  expect(exported.status, JSON.stringify(exported.body)).toBe(201);
  const body = exported.body as { directory: string; playerIncluded: boolean };
  expect(body.playerIncluded).toBe(true);
  packageDir = path.join(workspace, body.directory);
});

afterAll(async () => {
  await service?.stop();
});

describe('P5 发布包验收检查', () => {
  it('发布包通过完整性、素材齐全、敏感信息与本地路径检查，并可实际启动', async () => {
    const reportFile = path.join(workspace, 'release-check.json');
    const result = await run(process.execPath, [path.join('scripts', 'studio-release-check.mjs'), '--package', packageDir, '--json', reportFile, '--run']);
    expect(result.code, `${result.stdout}\n${result.stderr}`).toBe(0);
    const report = JSON.parse(readFileSync(reportFile, 'utf8')) as { summary: { passed: number; failed: number; warnings: number }; checks: { id: string; status: string; message: string }[]; fileCount: number; totalBytes: number };
    expect(report.summary.failed).toBe(0);
    const ids = report.checks.map((check) => check.id);
    for (const expected of ['RELEASE-FILES-PRESENT', 'RELEASE-HASHES', 'RELEASE-ASSET-COMPLETE', 'RELEASE-ASSET-HASH', 'RELEASE-NO-SECRETS', 'RELEASE-NO-LOCAL-DEPS', 'RELEASE-SMOKE']) {
      expect(ids, `缺少检查项 ${expected}`).toContain(expected);
    }
    expect(report.checks.find((check) => check.id === 'RELEASE-SMOKE')?.status).toBe('passed');
    trace('p5-release-check', { summary: report.summary, fileCount: report.fileCount, totalBytes: report.totalBytes, checks: report.checks.map((check) => `${check.id}:${check.status}`) });
  });

  it('包内文件被篡改时检查失败（散列不一致）', async () => {
    const assetPath = path.join(packageDir, 'content', 'assets', 'originals', 'as_p2bg000001.png');
    const original = readFileSync(assetPath);
    try {
      writeFileSync(assetPath, encodeSolidPng(12, 8, [255, 0, 0]));
      const result = await run(process.execPath, [path.join('scripts', 'studio-release-check.mjs'), '--package', packageDir]);
      expect(result.code).toBe(1);
      expect(result.stdout).toContain('RELEASE-HASHES');
      expect(result.stdout).toContain('as_p2bg000001.png');
    } finally {
      writeFileSync(assetPath, original);
    }
    const restored = await run(process.execPath, [path.join('scripts', 'studio-release-check.mjs'), '--package', packageDir]);
    expect(restored.code, restored.stdout).toBe(0);
  }, 60000);
});

describe('P5 旧 Studio v1 配置迁移', () => {
  let fixtureFile = '';
  let fixtureHash = '';
  let migrateWorkspace = '';

  beforeAll(() => {
    // 合成一份 v1 体验版工程：含嵌入图片素材、对白/选择/结束/关卡四类节点、一个工程内路径素材
    const embedded = `data:image/png;base64,${encodeSolidPng(6, 4, [40, 60, 90]).toString('base64')}`;
    const fixture = {
      version: 1,
      id: 'legacy_demo',
      title: '迁移测试工程',
      chapter: '旧章',
      entry: 'n1',
      assets: [
        { id: 'bg1', name: '旧背景', kind: 'background', src: embedded },
        { id: 'ch1', name: '旧人物', kind: 'character', src: embedded },
        { id: 'bg2', name: '外部背景', kind: 'background', src: '/studio/assets/external.png' },
      ],
      nodes: [
        { id: 'n1', title: '开场', type: 'dialogue', speaker: '璃', text: '这是一句旧台词。', background: 'bg1', character: 'ch1', next: 'n2' },
        { id: 'n2', title: '选择', type: 'choice', speaker: '', text: '', background: '', character: '', choices: [{ label: '进塔', next: 'n3' }, { label: '离开', next: 'n4' }] },
        { id: 'n3', title: '塔内', type: 'dialogue', speaker: '璃', text: '门后是石阶。', background: 'bg1', character: '', next: 'n5' },
        { id: 'n4', title: '结束 A', type: 'ending', speaker: '', text: '', background: '', character: '', locked: true },
        { id: 'n5', title: '星图', type: 'level', speaker: '', text: '', background: '', character: '', level: 'constellation', next: 'n4' },
      ],
    };
    fixtureFile = path.join(os.tmpdir(), `studio-v1-fixture-${Date.now()}.json`);
    writeFileSync(fixtureFile, JSON.stringify(fixture, null, 2), 'utf8');
    fixtureHash = sha256(readFileSync(fixtureFile));
  });

  afterAll(() => {
    if (fixtureFile && existsSync(fixtureFile)) rmSync(fixtureFile, { force: true });
    if (migrateWorkspace && existsSync(migrateWorkspace)) rmSync(migrateWorkspace, { recursive: true, force: true });
  });

  it('先预览迁移计划：列出支持与不支持的字段', async () => {
    const reportFile = path.join(os.tmpdir(), `studio-migrate-dry-${Date.now()}.json`);
    const result = await run(process.execPath, [path.join('scripts', 'studio-migrate.mjs'), '--input', fixtureFile, '--dry-run', '--json', reportFile]);
    expect(result.code, `${result.stdout}\n${result.stderr}`).toBe(0);
    const report = JSON.parse(readFileSync(reportFile, 'utf8')) as { supported: string[]; unsupported: string[]; warnings: string[]; nodeTypeCounts: Record<string, number>; embeddedAssets: number; externalAssets: number };
    expect(report.nodeTypeCounts.dialogue).toBe(2);
    expect(report.nodeTypeCounts.choice).toBe(1);
    expect(report.nodeTypeCounts.level).toBe(1);
    expect(report.embeddedAssets).toBe(2);
    expect(report.externalAssets).toBe(1);
    expect(report.supported.join(' ')).toContain('对白事件');
    expect(report.warnings.join(' ')).toContain('locked');
    rmSync(reportFile, { force: true });
    trace('p5-migrate-dry-run', { nodeTypeCounts: report.nodeTypeCounts, embeddedAssets: report.embeddedAssets, warnings: report.warnings });
  }, 60000);

  it('实际迁移并校验结果，且保留原始配置', async () => {
    migrateWorkspace = path.join(os.tmpdir(), `studio-migrate-ws-${Date.now()}`);
    mkdirSync(migrateWorkspace, { recursive: true });
    const reportFile = path.join(os.tmpdir(), `studio-migrate-report-${Date.now()}.json`);
    const result = await run(process.execPath, [path.join('scripts', 'studio-migrate.mjs'), '--input', fixtureFile, '--workspace', migrateWorkspace, '--keep-workspace', '--json', reportFile]);
    expect(result.code, `${result.stdout}\n${result.stderr}`).toBe(0);
    const report = JSON.parse(readFileSync(reportFile, 'utf8')) as {
      createdProjectId: string; revision: string; appliedOperations: number; validation: { ok: boolean; issueCount: number };
      warnings: string[]; sourcePreservedAt: string;
    };
    expect(report.validation.ok, JSON.stringify(report.validation)).toBe(true);
    expect(report.appliedOperations).toBeGreaterThanOrEqual(6);

    // 原始配置未被修改，并已被复制进工程 imports/ 供追溯
    expect(sha256(readFileSync(fixtureFile))).toBe(fixtureHash);
    const preserved = path.join(migrateWorkspace, report.sourcePreservedAt);
    expect(existsSync(preserved)).toBe(true);
    expect(sha256(readFileSync(preserved))).toBe(fixtureHash);

    // 迁移结果符合预期：对白/选择/结束都在，关卡单独列为警告
    const projectDir = path.join(migrateWorkspace, report.createdProjectId);
    expect(existsSync(path.join(projectDir, 'project.json'))).toBe(true);
    const scenesDir = path.join(projectDir, 'story', 'scenes');
    const sceneFile = path.join(scenesDir, readdirSync(scenesDir)[0]);
    const scene = JSON.parse(readFileSync(sceneFile, 'utf8')) as { events: { type: string; params: Record<string, unknown> }[] };
    const types = scene.events.map((event) => event.type);
    expect(types.filter((type) => type === 'dialogue')).toHaveLength(2);
    expect(types).toContain('choice');
    expect(types).toContain('ending');
    expect(types).toContain('background');
    expect(types).toContain('character');
    expect(report.warnings.join(' ')).toContain('关卡节点');
    expect(report.warnings.join(' ')).toContain('assets/originals');
    const assets = JSON.parse(readFileSync(path.join(projectDir, 'assets', 'manifest.json'), 'utf8')) as { assets: { assetId: string; provenance?: Record<string, unknown> }[] };
    expect(assets.assets).toHaveLength(2); // 嵌入的两张；外部路径素材未迁移
    trace('p5-migrate', { projectId: report.createdProjectId, revision: report.revision, operations: report.appliedOperations, warnings: report.warnings });
    rmSync(reportFile, { force: true });
  }, 120000);
});
