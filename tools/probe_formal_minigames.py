"""Exercise the new public STDIO MCP tools and retain exact requests/results."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


async def run(args):
    out = ROOT / 'evidence/html-minigames/public-jobs/runs' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    out.mkdir(parents=True)
    params = StdioServerParameters(command=sys.executable, args=['-m', 'renpy_bridge.server'], cwd=str(ROOT),
        env={**os.environ, 'RENPY_BRIDGE_HOME': str(ROOT), 'PYTHONUTF8': '1', 'PYTHONPATH': str(ROOT / 'src')})
    report = {'status': 'running', 'scope': 'actual_public_stdio_MCP_client', 'codex_plugin_remount_verified': False,
        'kind': args.kind, 'calls': [], 'model_revision': None, 'jobs': [],
        'core_files': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((ROOT / 'src/renpy_bridge').glob('*.py'))}}

    with (out / 'server.stderr.txt').open('w', encoding='utf-8') as server_log:
        return await run_session(args, out, params, report, server_log)


async def run_session(args, out, params, report, server_log):
    async with stdio_client(params, errlog=server_log) as (read, send):
        async with ClientSession(read, send) as session:
            await session.initialize()
            async def call(name, arguments, *, retain=True):
                result = await session.call_tool(name, arguments)
                if result.isError:
                    raise AssertionError(result.model_dump(mode='json'))
                value = json.loads(next(item.text for item in result.content if item.type == 'text'))
                if retain:
                    report['calls'].append({'tool': name, 'arguments': arguments, 'result': value})
                    write(out / 'report.json', report)
                if value.get('ok') is False:
                    raise AssertionError(value)
                return value
            listing = await session.list_tools()
            write(out / 'tools.json', [item.model_dump(mode='json', exclude_none=True) for item in listing.tools])
            project = await call('inspect_project', {'project_id': args.project_id})
            revision = project['revision']
            report['model_revision'] = revision
            scenarios = json.loads(args.scenarios.read_text(encoding='utf-8'))
            if not isinstance(scenarios, list) or not scenarios:
                raise AssertionError('Scenarios must be a nonempty list')
            requests = [('test_minigame', {'project_id': args.project_id, 'revision': revision, **item}) for item in scenarios] if args.kind == 'minigame' else [
                ('build_release' if args.kind == 'build' else 'test_routes', {'project_id': args.project_id, 'revision': revision, 'minigame_scenarios': scenarios})]
            for tool, arguments in requests:
                started = await call(tool, arguments)
                job_id = started['job_id']
                print(json.dumps({'stage': 'started', 'job_id': job_id, 'frame_id': arguments.get('frame_id'), 'report': str(out / 'report.json')}), flush=True)
                deadline = time.monotonic() + 1800
                while True:
                    status = await call('get_job', {'job_id': job_id}, retain=False)
                    if status['state'] in {'succeeded', 'failed', 'cancelled', 'interrupted'}:
                        final = await call('get_job', {'job_id': job_id, 'include_report': True, 'detail': 'full'})
                        report['jobs'].append(final)
                        write(out / 'report.json', report)
                        print(json.dumps({'stage': 'terminal', 'job_id': job_id, 'state': status['state'], 'report': final['report_path']}), flush=True)
                        if status['state'] != 'succeeded':
                            report['status'] = 'failed'
                            write(out / 'report.json', report)
                            return 1
                        break
                    if time.monotonic() > deadline:
                        await call('cancel_job', {'job_id': job_id})
                        raise TimeoutError('Bounded public job wait expired')
                    await asyncio.sleep(2)
    report['status'] = 'passed'
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    write(out / 'report.json', report)
    print(json.dumps({'status': 'passed', 'report': str(out / 'report.json'), 'jobs': len(report['jobs'])}), flush=True)
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--project-id', required=True)
    parser.add_argument('--scenarios', type=Path, required=True)
    parser.add_argument('--kind', choices=['minigame', 'routes', 'build'], default='minigame')
    sys.exit(asyncio.run(run(parser.parse_args())))
