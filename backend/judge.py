"""Actual Judge0 C11 execution. No local host execution and no simulated results."""
import asyncio
import base64
import os
import re
import time
import httpx

OUTPUT_LIMIT = 16 * 1024

class JudgeUnavailable(Exception):
    pass

def decode(value):
    return base64.b64decode(value or '').decode('utf-8', errors='replace')

class Judge:
    def __init__(self):
        self.url = os.environ.get('JUDGE0_URL', 'http://127.0.0.1:2358').rstrip('/')
        self.headers = {'X-Auth-Token': os.environ['JUDGE0_TOKEN']} if os.environ.get('JUDGE0_TOKEN') else {}
        self.language_id = None
        self.language_name = None
        self.health_cache = None
        self.health_at = 0

    async def discover(self, client):
        res = await client.get('/languages')
        res.raise_for_status()
        languages = [x for x in res.json() if x['name'].startswith('C (')]
        if not languages:
            raise JudgeUnavailable('执行器没有可用的 C 编译器。')
        gcc = [x for x in languages if 'GCC' in x['name']]
        preferred = max(gcc or languages, key=lambda x: tuple(map(int,re.findall(r'\d+',x['name']))))
        self.language_id, self.language_name = preferred['id'], preferred['name']

    async def health(self):
        if self.health_cache and time.monotonic() - self.health_at < 15:
            return self.health_cache
        try:
            async with httpx.AsyncClient(base_url=self.url, headers=self.headers, timeout=3) as client:
                await self.discover(client)
            probe = await asyncio.wait_for(self.execute('#include <stdio.h>\nint main(void){puts("ready");return 0;}',0),timeout=8)
            available = probe['status'] == 'finished' and probe['stdout'] == 'ready\n'
            result = {'available': available, 'compiler': self.language_name, 'standard': 'C11'}
        except Exception:
            result = {'available': False, 'compiler': self.language_name, 'standard': 'C11'}
        self.health_cache = result
        self.health_at = time.monotonic()
        return result

    async def execute(self, source: str, n: int):
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(base_url=self.url, headers=self.headers, timeout=8) as client:
                if self.language_id is None:
                    await self.discover(client)
                payload = {
                    'source_code': base64.b64encode(source.encode()).decode(),
                    'language_id': self.language_id,
                    'stdin': base64.b64encode(f'{n}\n'.encode()).decode(),
                    'compiler_options': '-std=c11 -O0',
                    'cpu_time_limit': 1, 'cpu_extra_time': 0, 'wall_time_limit': 3,
                    'memory_limit': 65536, 'max_file_size': 16,
                    'enable_network': False, 'number_of_runs': 1,
                    'redirect_stderr_to_stdout': False,
                }
                res = await client.post('/submissions?base64_encoded=true&wait=false', json=payload)
                res.raise_for_status()
                token = res.json()['token']
                while time.monotonic() - started < 20:
                    await asyncio.sleep(.25)
                    res = await client.get(f'/submissions/{token}?base64_encoded=true')
                    res.raise_for_status()
                    data = res.json()
                    if data['status']['id'] > 2:
                        break
                else:
                    raise JudgeUnavailable('执行服务排队超时，请稍后重试。')
        except JudgeUnavailable:
            raise
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise JudgeUnavailable('执行服务暂不可用。代码草稿已保留，请检查 Judge0 后重试。') from exc
        stdout, stderr, compile_output = (decode(data.get(key)) for key in ('stdout', 'stderr', 'compile_output'))
        status_id = data['status']['id']
        kind = {3: 'finished', 5: 'timeout', 6: 'compile_error', 13: 'service_unavailable', 14: 'service_unavailable'}.get(status_id, 'runtime_error')
        # SIGXFSZ is signal 25 on Linux. Treat reaching the cap as overflow even if exit=0.
        if len(stdout.encode()) >= OUTPUT_LIMIT or len(stderr.encode()) >= OUTPUT_LIMIT or data.get('exit_signal') == 25:
            kind = 'output_limit'
        return {'status': kind, 'stdout': stdout.encode()[:OUTPUT_LIMIT].decode(errors='replace'),
                'stderr': stderr.encode()[:OUTPUT_LIMIT].decode(errors='replace'), 'compile_output': compile_output[:OUTPUT_LIMIT],
                'duration_ms': round((time.monotonic() - started) * 1000),
                'executor_status': data['status']['description']}
