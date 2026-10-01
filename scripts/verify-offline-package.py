"""Run known C probes using only the tools in an extracted Windows package."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.stdout.reconfigure(encoding='utf-8')
root=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(root/'game/python-packages'))
from local_runner import Runner,build_source,source_hash

runner=Runner(root)
report={'root':str(root),'runtime':runner.check_runtime(),'tests':[]}
assert report['runtime']['available'],report
for tid,fragment,expected in [
    ('tower_light_01','0','wrong_answer'),
    ('tower_light_01','1','passed'),
    ('tower_light_02','i<n','wrong_answer'),
    ('tower_light_02','i<n+1','passed'),
    ('tower_light_03','for(int i=1;i<=n;i++){printf("%d\\n",i);}','passed'),
    ('tower_light_03','for(int i=1;i<=n;i++){printf("%d\\n",i)}','compile_error'),
    ('tower_light_03','while(1){}','timeout'),
    ('tower_light_03','while(1){printf("12345678901234567890\\n");}','output_limit')
]:
    start=time.monotonic()
    req=dict(run_id='package-test',client_request_id=uuid.uuid4().hex,task_id=tid,task_revision='tower-c11-1.1',mode='verify',editable_fragment=fragment,source_hash=source_hash(build_source(tid,fragment)),trial_input=1 if tid.endswith('01') else 3)
    sid=runner.start_submission(req)
    while runner.poll_submission(sid)['status']!='finished':
        assert time.monotonic()-start<35
        time.sleep(.02)
    result=runner.poll_submission(sid)['result']
    report['tests'].append(dict(expected=expected,elapsed=round(time.monotonic()-start,3),result=result))
    assert result['status']==expected,result
    print(tid,expected,round(time.monotonic()-start,3),'seconds')
runner.cancel_all()
dest=Path(__file__).resolve().parents[1]/'docs/renpy/package-verification.json'
dest.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('8 real packaged-runtime probes passed. Report:',dest)
