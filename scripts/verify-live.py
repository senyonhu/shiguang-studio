"""Runs real compiler/sandbox acceptance checks. Requires API + Judge0 running."""
import json
import time
from pathlib import Path
import httpx

BASE='http://127.0.0.1:8000'
LOOP='#include <stdio.h>\nint main(void){int n;scanf("%d",&n);for(int i=1;i<=n;i++)printf("%d\\n",i);return 0;}'
checks=[
 ('first_wrong','tower_light_01','#include <stdio.h>\nint main(void){printf("0\\n");}', 'wrong_answer'),
 ('first_correct','tower_light_01','#include <stdio.h>\nint main(void){printf("1\\n");}', 'passed'),
 ('syntax_error','tower_light_01','int main(void){ this is invalid C; }','compile_error'),
 ('loop_missing_last','tower_light_02',LOOP.replace('i<=n','i<n'),'wrong_answer'),
 ('loop_correct','tower_light_02',LOOP,'passed'),
 ('all_cases','tower_light_03',LOOP,'passed'),
 ('hardcoded','tower_light_03','#include <stdio.h>\nint main(void){puts("1\\n2\\n3");}','wrong_answer'),
 ('wrong_order','tower_light_02','#include <stdio.h>\nint main(void){puts("3\\n2\\n1");}','wrong_answer'),
 ('duplicates','tower_light_02','#include <stdio.h>\nint main(void){puts("1\\n2\\n2\\n3");}','wrong_answer'),
 ('extra_text','tower_light_01','#include <stdio.h>\nint main(void){puts("1\\nhello");}','wrong_answer'),
 ('timeout','tower_light_01','int main(void){while(1){}return 0;}','timeout'),
 ('runtime_error','tower_light_01','#include <stdlib.h>\nint main(void){abort();}','runtime_error'),
 ('overflow','tower_light_01','#include <stdio.h>\nint main(void){for(int i=0;i<50000;i++)puts("123456789");}','output_limit'),
]
report=[]
with httpx.Client(timeout=15) as client:
    for _ in range(12):
        health=client.get(BASE+'/api/health').json()
        if health['executor']['available']:
            break
        time.sleep(3)
    print(json.dumps(health),flush=True)
    assert health['executor']['available'], 'Real Judge0 is not available'
    for name,task,source,expected in checks:
        r=client.post(BASE+'/api/submissions',json={'task_id':task,'source':source,'mode':'submit'})
        r.raise_for_status(); data=r.json();started=time.monotonic()
        while data['status'] in ('queued','running') and time.monotonic()-started<110:
            time.sleep(.4)
            data=client.get(BASE+'/api/submissions/'+data['submission_id']).json()
        passed=data['status']==expected
        report.append({'name':name,'expected':expected,'actual':data['status'],'ok':passed,'result':data})
        print(f'{name}: {data["status"]} / expected {expected}',flush=True)
Path('.runtime').mkdir(exist_ok=True)
Path('.runtime/live-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
assert all(item['ok'] for item in report), 'Some real execution checks failed. See .runtime/live-verification.json'
print(f'{len(report)} real execution checks passed.')
