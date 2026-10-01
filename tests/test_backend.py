import asyncio
import pytest
from fastapi.testclient import TestClient
from backend.tasks import matches
from backend.app import app

@pytest.mark.parametrize('stdout,n,passed',[
    ('1\n',1,True),('1',1,True),('0\n',1,False),('1\r\n2\r\n3\r\n',3,True),
    ('',0,True),('\n',0,False),('1\n2\n',3,False),('1\n2\n3\n',3,True),
    ('1\n2\n3\n\n',3,False),('1\n\n2\n3',3,False),('1 \n2\n3',3,False),
    ('3\n2\n1',3,False),('1\n2\n2\n3',3,False),('1\n2\n3\nhello',3,False),
    ('01\n',1,False),('+1\n',1,False),('1\r',1,False),
])
def test_protocol(stdout,n,passed):
    assert matches(stdout,n) is passed

def test_api_validation():
    with TestClient(app) as client:
        assert client.get('/api/tasks/nope').status_code == 404
        base={'task_id':'tower_light_03','source':'int main(void) { return 0; }','mode':'run'}
        assert client.post('/api/submissions',json={**base,'input':7}).status_code == 422
        assert client.post('/api/submissions',json={**base,'input':1.5}).status_code == 422
        assert client.post('/api/submissions',json={**base,'enable_network':True}).status_code == 422
        assert client.post('/api/submissions',json={**base,'source':'x'*16001}).status_code == 422

@pytest.mark.asyncio
@pytest.mark.parametrize('task_id,mode,n,expected_inputs',[
    ('tower_light_02','run',0,[0]),('tower_light_02','run',4,[4]),
    ('tower_light_03','run',0,[0]),('tower_light_03','run',6,[6]),
    ('tower_light_02','submit',6,[3]),('tower_light_03','submit',2,[0,1,3,6]),
    ('tower_light_01','run',6,[1]),
])
async def test_run_input_and_fixed_submission_cases(monkeypatch,task_id,mode,n,expected_inputs):
    from backend import app as module
    captured=[]
    async def evaluate(record,source,inputs):
        captured.extend(inputs)
    monkeypatch.setattr(module,'bounded_evaluate',evaluate)
    await module.submit(module.SubmissionRequest(task_id=task_id,source='int main(void){return 0;}',mode=mode,input=n))
    await asyncio.gather(*module.jobs)
    assert captured == expected_inputs

@pytest.mark.asyncio
async def test_snapshot_and_cases(monkeypatch):
    from backend import app as module
    captured=[]
    async def execute(source,n):
        captured.append((source,n))
        return {'status':'finished','stdout':'\n'.join(map(str,range(1,n+1))), 'stderr':'','compile_output':'','duration_ms':1}
    monkeypatch.setattr(module.judge,'execute',execute)
    record={'status':'queued','cases':[]}
    await module.evaluate(record,'immutable-source',(0,1,3,6))
    assert record['passed'] and len(captured)==4
    assert {source for source,_ in captured}=={'immutable-source'}
    assert record['cases'][0]['passed'] and record['cases'][0]['stdout']==''

@pytest.mark.asyncio
async def test_unavailable_never_passes(monkeypatch):
    from backend import app as module
    from backend.judge import JudgeUnavailable
    async def execute(source,n):
        raise JudgeUnavailable('offline')
    monkeypatch.setattr(module.judge,'execute',execute)
    record={'status':'queued','cases':[]}
    await module.evaluate(record,'source',(0,1,3,6))
    assert not record['passed']
    assert record['status']=='service_unavailable'
