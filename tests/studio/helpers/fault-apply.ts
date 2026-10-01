// 故障注入辅助进程：在 journal 提交的指定步骤杀死自己，用于验证崩溃恢复。
// 用法：STUDIO_FAULT_ENABLE=1 STUDIO_FAULT_POINT=after_state node tests/studio/helpers/fault-apply.ts --workspace <ws> --project <id> --scene <id> --text <t> --base-revision <rev>
import path from 'node:path';
import { writeFileSync } from 'node:fs';
import { EditService } from '../../../server/src/edit/service.ts';
import { StudioError } from '../../../server/src/core/errors.ts';

function value(flag: string, fallback: string): string {
  const index = process.argv.indexOf(flag);
  return index >= 0 && index + 1 < process.argv.length ? process.argv[index + 1] : fallback;
}

async function main(): Promise<void> {
  const workspaceRoot = path.resolve(value('--workspace', '.'));
  const service = new EditService({
    workspaceRoot,
    importRoots: [path.join(workspaceRoot, 'imports')],
  });
  const projectId = value('--project', '');
  const sceneId = value('--scene', '');
  const text = value('--text', '故障注入对白');
  const baseRevision = value('--base-revision', '');
  // 标记文件证明子进程确实走到了提交阶段（而不是因为脚本错误提前退出）。
  writeFileSync(path.join(workspaceRoot, '.fault-apply-started'), `${new Date().toISOString()} point=${process.env.STUDIO_FAULT_POINT ?? ''}\n`, 'utf8');
  try {
    const result = service.applyTransaction(
      { actorId: 'ai:fault-injector', label: 'fault-injector', sessionId: 'sess_fault', channel: 'cli', scopes: ['read', 'edit'] },
      {
        requestId: value('--request-id', `fault-${Date.now()}`),
        projectId,
        baseRevision,
        operations: [{ action: 'event.insert', payload: { sceneId, type: 'dialogue', params: { speaker: '璃', text } } }],
      },
    );
    process.stdout.write(`${JSON.stringify({ completed: true, result })}\n`);
  } catch (error) {
    const code = error instanceof StudioError ? error.code : 'ERROR';
    process.stdout.write(`${JSON.stringify({ completed: false, code, message: (error as Error).message })}\n`);
    process.exitCode = 2;
  }
}

void main();
