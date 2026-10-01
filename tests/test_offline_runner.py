import sys
from pathlib import Path
import time
import uuid
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'client/game/python-packages'))
from local_runner import Runner, build_source, compare_output, source_hash

@pytest.mark.parametrize('actual,n,passed', [('',0,True), ('\n',0,False), ('1',1,True), ('1\r\n2\r\n3\r\n',3,True), ('1\n2\n',3,False), ('1\n1\n2\n3',3,False), ('3\n2\n1',3,False), ('01',1,False), ('+1',1,False), ('1 ',1,False), ('1\n\n',1,False)])
def test_exact_protocol(actual,n,passed):
    assert compare_output(actual,n)['passed'] == passed

@pytest.mark.parametrize('fragment', ['#include "C:/secret"', '%:include <stdio.h>', '??=include <stdio.h>', 'i\\\n<n', 'system("test");', 'asm("test");', 'fopen("file", "r");'])
def test_fragment_boundary(fragment):
    with pytest.raises(ValueError):
        build_source('tower_light_03',fragment)

def request(tid,fragment,mode='verify',n=3):
    return dict(run_id='test', client_request_id=uuid.uuid4().hex, task_id=tid, task_revision='tower-c11-1.1', mode=mode, editable_fragment=fragment, source_hash=source_hash(build_source(tid,fragment)), trial_input=1 if tid.endswith('01') else n)

def wait(runner,sid):
    deadline=time.monotonic()+35
    while time.monotonic()<deadline:
        result=runner.poll_submission(sid)
        if result['status']=='finished':
            return result['result']
        time.sleep(.02)
    raise AssertionError('runner did not finish')

@pytest.fixture
def runner():
    value=Runner(ROOT/'client')
    assert value.check_runtime()['available'],value.check_runtime()
    yield value
    value.cancel_all()

@pytest.mark.parametrize('tid,fragment,status', [
    ('tower_light_01','0','wrong_answer'),
    ('tower_light_01','1','passed'),
    ('tower_light_02','i < n','wrong_answer'),
    ('tower_light_02','i <= n','passed'),
    ('tower_light_02','i < n + 1','passed'),
    ('tower_light_03','for(int i=1;i<=n;i++){printf("%d\\n",i);}','passed'),
    ('tower_light_03','for(int i=1;i<=n;i++){printf("%d\\n",i)}','compile_error'),
    ('tower_light_03','while(1){}','timeout'),
    ('tower_light_03','while(1){printf("123456789012345678901234567890\\n");}','output_limit'),
])
def test_real_execution(runner,tid,fragment,status):
    result=wait(runner,runner.start_submission(request(tid,fragment)))
    assert result['status']==status,result
    if tid.endswith('03') and status=='passed':
        assert [c['input'] for c in result['cases']]==[0,1,3,6]
        assert result['cases'][0]['stdout']==''

def test_idempotence_and_cancel(runner):
    req=request('tower_light_03','while(1){}')
    sid=runner.start_submission(req)
    assert runner.start_submission(req)==sid
    with pytest.raises(ValueError,match='冲突'):
        runner.start_submission(dict(req,mode='trial'))
    runner.cancel_submission(sid)
    assert wait(runner,sid)['status']=='cancelled'
    assert wait(runner,runner.start_submission(request('tower_light_01','1')))['passed']

def test_missing_component(tmp_path):
    r=Runner(tmp_path)
    assert not r.check_runtime()['available']
    assert wait(r,r.start_submission(request('tower_light_01','1')))['status']=='unavailable'

def test_job_kills_child_tree(tmp_path):
    import ctypes
    from ctypes import wintypes
    import threading
    from local_runner.process import bounded_process
    code = 'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"]); print(p.pid,flush=True); time.sleep(30)'
    result=bounded_process([sys.executable,'-c',code],b'',0.6,4096,threading.Event(),str(tmp_path))
    assert result['status']=='timeout',result
    pid=int(result['stdout'].strip())
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x00100000,False,pid)
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle,1000)==0,'child process survived Job termination'
        finally:
            kernel.CloseHandle(handle)

def test_progress_atomic_backup_and_dedup(tmp_path):
    from local_runner.progress import Progress
    import json
    p=Progress(tmp_path)
    p.draft('run','tower_light_03','first',1)
    p.draft('run','tower_light_03','first',1,before_reset=True)
    p.draft('run','tower_light_03','second',2)
    assert p.latest('run','tower_light_03')['fragment']=='second'
    assert json.loads((tmp_path/'draft-run-tower_light_03-before-reset.json').read_text())['fragment']=='first'
    for _ in range(2):
        p.event('run','task_completed',event_id='run:03:completed',fragment='source')
    path=p.export('run',{},include_code=False)
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    assert len(data['events'])==1
    assert 'fragment' not in data['events'][0]
