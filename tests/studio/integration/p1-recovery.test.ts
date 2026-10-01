// P1 崩溃恢复集成测试：在 journal 提交的各个步骤注入进程被杀，验证重启后工程处于一致状态。
// 每个故障点使用独立工作区；恢复通过“重启真实后台进程”触发，再用 HTTP API 读取结果。
import { describe, expect, it } from 'vitest';
import { spawn } from 'node:child_process';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import {
  REPO_ROOT, api, delay, makeWorkspace, pairSession, readPairingKey, startBackend, trace,
} from '../helpers/harness.ts';

const FAULT_HELPER = path.join(REPO_ROOT, 'tests', 'studio', 'helpers', 'fault-apply.ts');
const FAULT_POINTS = ['after_backup', 'during_content', 'after_content', 'after_history', 'after_state', 'after_marker'];

function runFaultChild(workspace: string, point: string, args: { projectId: string; sceneId: string; baseRevision: string; text: string; requestId: string }): Promise<{ code: number | null; signal: string | null; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [
      FAULT_HELPER, '--workspace', workspace, '--project', args.projectId, '--scene', args.sceneId,
      '--text', args.text, '--base-revision', args.baseRevision, '--request-id', args.requestId,
    ], {
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, STUDIO_FAULT_ENABLE: '1', STUDIO_FAULT_POINT: point },
    });
    let stdout = '';
    let stderr = '';
    child.stdout?.on('data', (chunk: Buffer) => { stdout += chunk.toString(); });
    child.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });
    child.on('close', (code, signal) => resolve({ code, signal, stdout, stderr }));
  });
}

function readStatus(workspace: string, projectId: string): { revision: string; events: number; text: string[] } {
  const state = JSON.parse(readFileSync(path.join(workspace, projectId, '.studio', 'state.json'), 'utf8')) as { revision: string };
  const scenesDir = path.join(workspace, projectId, 'story', 'scenes');
  const events: { type: string; params: { text?: string } }[] = [];
  for (const name of readdirSync(scenesDir)) {
    if (!name.endsWith('.json')) continue;
    const scene = JSON.parse(readFileSync(path.join(scenesDir, name), 'utf8')) as { events: { type: string; params: { text?: string } }[] };
    for (const event of scene.events) if (event.type === 'dialogue') events.push(event);
  }
  return { revision: state.revision, events: events.length, text: events.map((event) => event.params.text ?? '') };
}

function listJournal(workspace: string, projectId: string): { name: string; files: string[] }[] {
  const dir = path.join(workspace, projectId, '.studio', 'journal');
  if (!existsSync(dir)) return [];
  return readdirSync(dir).map((name) => ({ name, files: readdirSync(path.join(dir, name)) }));
}

function findTempLeftovers(workspace: string, projectId: string): string[] {
  const found: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const abs = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(abs);
      else if (entry.name.includes('.tmp-')) found.push(path.relative(workspace, abs));
    }
  };
  walk(path.join(workspace, projectId));
  return found;
}

describe('P1 崩溃恢复：journal 各阶段被杀后重新打开工程', () => {
  for (const point of FAULT_POINTS) {
    it(`故障点 ${point}：恢复后工程一致，无残留临时文件`, async () => {
      const workspace = makeWorkspace(`crash-${point}`);
      let service = await startBackend(workspace, { name: `crash-${point}` });
      const session = await pairSession(service.url, readPairingKey(workspace), `crash-${point}`);
      const created = await api(service.url, session.token, 'POST', '/v1/projects', { title: '崩溃恢复工程', chapterTitle: '第一章', sceneTitle: '开场' });
      const { projectId, sceneId } = created.body as { projectId: string; sceneId: string };
      // 先写入一条对白，作为“故障前的一致状态”
      const baseline = await api(service.url, session.token, 'POST', `/v1/projects/${projectId}/transactions`, {
        requestId: 'baseline-dialogue',
        projectId,
        baseRevision: '0',
        operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '故障前已经保存的对白' } } }],
      });
      const before = (baseline.body as { revision: string }).revision;
      expect(before).toBe('1');

      await service.stop('SIGKILL');
      await delay(200);

      const fault = await runFaultChild(workspace, point, {
        projectId, sceneId, baseRevision: before, text: '故障注入时写入的对白', requestId: `fault-${point}`,
      });
      // 故障子进程必须确实进入提交阶段，且不是因为脚本/模块错误退出。
      expect(existsSync(path.join(workspace, '.fault-apply-started'))).toBe(true);
      expect(fault.stderr).toBe('');
      expect(fault.stdout.trim()).toBe('');
      expect(fault.code === 0).toBe(false);

      const midway = readStatus(workspace, projectId);
      trace('crash-midway', { point, midway, faultExit: fault.code, faultSignal: fault.signal });

      service = await startBackend(workspace, { name: `crash-${point}-recovered` });
      const recovered = readStatus(workspace, projectId);
      const expectsCommit = point === 'after_marker';
      if (expectsCommit) {
        expect(recovered.revision).toBe('2');
        expect(recovered.events).toBe(2);
      } else {
        expect(recovered.revision).toBe('1');
        expect(recovered.events).toBe(1);
        expect(recovered.text).toEqual(['故障前已经保存的对白']);
      }
      expect(findTempLeftovers(workspace, projectId)).toEqual([]);
      const journal = listJournal(workspace, projectId);
      expect(journal.length).toBeGreaterThan(0);
      expect(journal.every((entry) => entry.files.includes('committed.json') || entry.files.includes('rolled_back.json'))).toBe(true);

      // 恢复后必须能继续写入：新事务基于恢复后的修订提交成功
      const after = await pairSession(service.url, readPairingKey(workspace), `crash-${point}-post`);
      const next = await api(service.url, after.token, 'POST', `/v1/projects/${projectId}/transactions`, {
        requestId: `post-recovery-${point}`,
        projectId,
        baseRevision: recovered.revision,
        operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text: '恢复之后写入的对白' } } }],
      });
      expect((next.body as { status: string }).status).toBe('applied');
      const validation = await api(service.url, after.token, 'GET', `/v1/projects/${projectId}/validate`);
      expect((validation.body as { ok: boolean }).ok).toBe(true);

      trace('crash-recovery', {
        point,
        revisionBeforeFault: before,
        revisionAfterRecovery: recovered.revision,
        eventsAfterRecovery: recovered.events,
        expectedCommit: expectsCommit,
        journal,
        revisionAfterNextTransaction: (next.body as { revision: string }).revision,
      });
      await service.stop();
    }, 120000);
  }

  it('同一工作区不能同时运行两个后台实例', async () => {
    const workspace = makeWorkspace('single-instance');
    const first = await startBackend(workspace, { name: 'lock-first' });
    const second = spawn(process.execPath, [path.join(REPO_ROOT, 'server', 'src', 'main.ts'), '--workspace', workspace, '--port', '0'], { stdio: ['ignore', 'pipe', 'pipe'] });
    let stderr = '';
    second.stderr?.on('data', (chunk: Buffer) => { stderr += chunk.toString(); });
    const code: number | null = await new Promise((resolve) => second.on('close', (value) => resolve(value)));
    expect(code).not.toBe(0);
    expect(stderr).toContain('SERVICE_ALREADY_RUNNING');
    const health = await fetch(`${first.url}/v1/health`).then((response) => response.json()) as { instanceId: string };
    expect(health.instanceId).toBe(first.instanceId);
    trace('single-instance', { firstInstance: first.instanceId, secondExitCode: code, stderr: stderr.slice(0, 400) });
    await first.stop();
  }, 60000);
});
