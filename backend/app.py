import asyncio
import hashlib
import time
import uuid
from typing import Literal
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from backend.tasks import TASKS, expected, matches
from backend.judge import Judge, JudgeUnavailable

app = FastAPI(title='旧塔的最后一盏灯', version='0.1.0')
judge = Judge()
submissions: dict[str, dict] = {}
jobs: set[asyncio.Task] = set()
slots = asyncio.Semaphore(2)

class SubmissionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    task_id: str
    source: str = Field(min_length=1, max_length=16000)
    mode: Literal['run', 'submit']
    input: int | None = Field(default=None, ge=0, le=6)

@app.get('/api/health')
async def health():
    return {'service': 'ok', 'executor': await judge.health()}

@app.get('/api/tasks/{task_id}')
async def task_info(task_id: str):
    task = TASKS.get(task_id)
    if not task:
        raise HTTPException(404, '任务不存在')
    return {'id': task.id, 'title': task.title, 'initial_source': task.source, 'inputs': task.inputs}

async def evaluate(record: dict, source: str, inputs: tuple[int, ...]):
    async with slots:
        record['status'] = 'running'
        for n in inputs:
            try:
                result = await judge.execute(source, n)
            except JudgeUnavailable as exc:
                result = {'status': 'service_unavailable', 'stdout': '', 'stderr': str(exc), 'compile_output': '', 'duration_ms': 0}
            passed = result['status'] == 'finished' and matches(result['stdout'], n)
            if result['status'] == 'finished':
                result['status'] = 'passed' if passed else 'wrong_answer'
            case = {**result, 'input': n, 'expected': expected(n), 'passed': passed}
            record['cases'].append(case)
            if result['status'] == 'service_unavailable':
                break
        record['passed'] = len(record['cases']) == len(inputs) and all(c['passed'] for c in record['cases'])
        failed = next((c for c in record['cases'] if not c['passed']), None)
        record['status'] = 'passed' if record['passed'] else failed['status']
        record['error_kind'] = None if record['passed'] else record['status']
        representative = failed or record['cases'][-1]
        for key in ('stdout', 'stderr', 'compile_output'):
            record[key] = representative[key]
        record['duration_ms'] = sum(c['duration_ms'] for c in record['cases'])

async def bounded_evaluate(record, source, inputs):
    try:
        await asyncio.wait_for(evaluate(record, source, inputs), timeout=100)
    except Exception:
        record.update(status='service_unavailable', passed=False, error_kind='service_unavailable', stderr='执行服务请求超时或异常，请重试。')

@app.post('/api/submissions', status_code=202)
async def submit(request: SubmissionRequest):
    task = TASKS.get(request.task_id)
    if task is None:
        raise HTTPException(404, '任务不存在')
    if len(jobs) >= 12:
        raise HTTPException(429, '执行队列已满，请稍后重试')
    # Fixed task inputs, and one immutable source snapshot for all four final cases.
    inputs = task.inputs if request.mode == 'submit' else (request.input if task.id != 'tower_light_01' and request.input is not None else task.inputs[-1],)
    submission_id = str(uuid.uuid4())
    record = {'submission_id': submission_id, 'task_id': task.id,
              'source_hash': hashlib.sha256(request.source.encode()).hexdigest(), 'status': 'queued',
              'mode': request.mode, 'stdout': '', 'stderr': '', 'compile_output': '', 'cases': [],
              'duration_ms': 0, 'passed': False, 'error_kind': None, 'created_at': time.time()}
    # Bound memory; stable progress lives in the browser, not these temporary jobs.
    for key, value in list(submissions.items()):
        if time.time() - value['created_at'] > 3600 and value['status'] not in ('queued', 'running'):
            del submissions[key]
    if len(submissions) >= 500:
        for key, value in list(submissions.items()):
            if value['status'] not in ('queued', 'running'):
                del submissions[key]
                break
    submissions[submission_id] = record
    job = asyncio.create_task(bounded_evaluate(record, request.source, inputs))
    jobs.add(job)
    job.add_done_callback(jobs.discard)
    return dict(record)

@app.get('/api/submissions/{submission_id}')
async def result(submission_id: str):
    if submission_id not in submissions:
        raise HTTPException(404, '提交记录已过期，请重新运行；代码草稿不受影响')
    return submissions[submission_id]
