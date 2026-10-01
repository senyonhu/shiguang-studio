import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  writes: vi.fn(), copies: vi.fn(), copyTree: vi.fn(), mkdir: vi.fn(), mkdtemp: vi.fn(() => 'C:/temporary/studio-build-fixture'),
  close: vi.fn(async () => {}), startService: vi.fn(), buildStory: vi.fn(), install: vi.fn(() => true),
}));
vi.mock('node:fs', () => ({
  cpSync: mocks.copies, existsSync: vi.fn(() => false), mkdirSync: mocks.mkdir, mkdtempSync: mocks.mkdtemp,
  readFileSync: vi.fn((file: string) => file.endsWith('package.json') ? JSON.stringify({ version: '0.1.0' }) : JSON.stringify({ pairingKey: 'fixture-pairing' })),
  writeFileSync: mocks.writes,
}));
vi.mock('node:child_process', () => ({ spawnSync: vi.fn(() => ({ status: 0 })) }));
vi.mock('../../../server/src/main.ts', () => ({ startService: mocks.startService }));
vi.mock('../../../server/src/publisher/service.ts', () => ({ exportProject: vi.fn(() => ({ directory: 'prj_fixture/exports/exp-fixture', playerIncluded: true, portableIncluded: true, sensitiveHits: [], missingAssets: [], missingLevels: [] })) }));
vi.mock('../../../server/src/publisher/portable.ts', () => ({ installPlayerPortable: mocks.install }));
vi.mock('../../studio/helpers/story.ts', () => ({ buildStory: mocks.buildStory, LANTERN_LEVEL_HTML: '<html><head></head><body></body></html>' }));
vi.mock('../../../scripts/studio-portable-lib.mjs', () => ({ copyTree: mocks.copyTree, refreshManifest: vi.fn(() => ({ fileCount: 1 })), zipDirectory: vi.fn((directory: string) => `${directory}.zip`) }));
vi.mock('../../../scripts/studio-lib.mjs', () => ({ argValue: vi.fn((_argv: string[], _key: string, fallback: string) => fallback) }));

const originalArgv = process.argv;
beforeEach(() => {
  vi.clearAllMocks();
  vi.resetModules();
  mocks.startService.mockResolvedValue({ url: 'http://127.0.0.1:1234', close: mocks.close });
  mocks.buildStory.mockResolvedValue({ projectId: 'prj_fixture', levelId: 'lv_fixture', revision: '1' });
  vi.stubGlobal('fetch', vi.fn(async () => ({ json: async () => ({ token: 'fixture-token' }) })));
  vi.spyOn(console, 'log').mockImplementation(() => {});
});
afterEach(() => { process.argv = originalArgv; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('便携构建模式', () => {
  it('默认不启动示例服务、不生成seed或凭据，编辑器与展示器均为空白', async () => {
    process.argv = ['node', 'studio-portable-build.mjs'];
    // @ts-expect-error The side-effect-only JavaScript build entry has no declaration file.
    await import('../../../scripts/studio-portable-build.mjs');
    expect(mocks.startService).not.toHaveBeenCalled();
    expect(mocks.buildStory).not.toHaveBeenCalled();
    expect(mocks.mkdtemp).not.toHaveBeenCalled();
    expect(mocks.install).toHaveBeenCalledOnce();
    const destinations = [...mocks.writes.mock.calls, ...mocks.copies.mock.calls, ...mocks.copyTree.mock.calls, ...mocks.mkdir.mock.calls].map((call) => String(call[0]) + String(call[1] ?? ''));
    expect(destinations.some((value) => /seed-workspace|studio-service|pairing\.key/.test(value))).toBe(false);
    expect(destinations.some((value) => value.includes('放置作品说明.txt'))).toBe(true);
    expect(JSON.parse(String(vi.mocked(console.log).mock.calls[0][0])).sampleProjectId).toBeNull();
  });
  it('--with-demo同时生成编辑器首启示例和展示器作品，seed过滤服务与历史数据', async () => {
    process.argv = ['node', 'studio-portable-build.mjs', '--with-demo'];
    // @ts-expect-error The side-effect-only JavaScript build entry has no declaration file.
    await import('../../../scripts/studio-portable-build.mjs');
    expect(mocks.startService).toHaveBeenCalledOnce();
    expect(mocks.buildStory).toHaveBeenCalledOnce();
    expect(mocks.close).toHaveBeenCalledOnce();
    const seedCopy = mocks.copyTree.mock.calls.find((call) => String(call[1]).includes('seed-workspace'));
    expect(seedCopy).toBeDefined();
    const filter = seedCopy![2] as (relative: string) => boolean;
    expect(filter('.studio/state.json')).toBe(true);
    expect(filter('.studio/history/txn.json')).toBe(false);
    expect(filter('exports/old/content.json')).toBe(false);
    expect(mocks.copyTree.mock.calls.some((call) => String(call[1]).includes('Player-Demo'))).toBe(true);
    expect(JSON.parse(String(vi.mocked(console.log).mock.calls[0][0])).sampleProjectId).toBe('prj_fixture');
  });
});
