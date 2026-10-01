import { computed, reactive, ref, toRaw } from 'vue';
import { Story } from 'inkjs';
import storyJson from './generated/story.json';
import { fresh, readSave, writeSave, validateSave } from './save';
import { ids, lightEvents, tasks } from './tasks';
import { initAudio, mute, sound, worldAudio, suspendAudio, resumeAudio } from './audio';
import type { Result, Save, TaskId } from './types';

export function useGame(){
 const s=reactive<Save>(fresh());
 const screen=ref<Save['screen']>('title'),saved=ref<Save|null>(null),notice=ref(''),busy=ref(false),result=ref<Result|null>(null),dirty=ref(false),lit=ref<number[]>([]),flash=ref(0),playing=ref(false),input=ref(3),health=ref<'checking'|'online'|'offline'>('checking'),settingsOpen=ref(false),hintOpen=ref(false),quiz=ref(false),quizFeedback=ref(''),practice=ref(false);
 let ink=new Story(storyJson),epoch=0,replayEpoch=0,saveTimer:ReturnType<typeof setTimeout>,cinemaTimer:ReturnType<typeof setTimeout>;
 const task=computed(()=>tasks[s.current_task_id]);
 const taskNumber=computed(()=>ids.indexOf(s.current_task_id)+1);
 const lightCount=computed(()=>screen.value==='task'?(taskNumber.value===1?1:Number.isInteger(input.value)&&input.value>=0&&input.value<=6?input.value:0):6);
 const ready=computed(()=>s.completed_tasks.includes(s.current_task_id)&&!practice.value);
 const clone=()=>JSON.parse(JSON.stringify(toRaw(s))) as Save;
 function log(event:string,detail=''){if(s.debug)return;s.records.push({at:new Date().toISOString(),event,task:s.current_task_id,detail});if(s.records.length>2000)s.records.shift();}
 async function persist(){clearTimeout(saveTimer);s.save_revision++;s.saved_at=new Date().toISOString();s.ink_state=ink.state.ToJson();try{const snapshot=clone();await writeSave(snapshot);if(!s.debug)saved.value=snapshot;return true;}catch{notice.value='本地存档写入失败，请导出存档备份。进度尚未持久化。';return false;}}
 function scheduleSave(){clearTimeout(saveTimer);saveTimer=setTimeout(()=>void persist(),250);}
 function nextLine(){
   if(!ink.canContinue)return;
   s.line=(ink.Continue()??'').trim(); s.choices=ink.currentChoices.map(c=>({text:c.text,index:c.index}));
   for(const tag of ink.currentTags??[]){
    if(tag.startsWith('face:'))s.face=tag.slice(5);
    if(tag.startsWith('task:')){const id=tag.slice(5) as TaskId;s.current_task_id=id;s.screen='task';input.value=id===ids[2]?6:id===ids[1]?3:1;result.value=null;dirty.value=false;lit.value=[];hintOpen.value=false;log('task_enter');}
    if(tag==='cinematic')s.screen='cinematic';
    if(tag==='ending')s.screen='ending';
   }
 }
 async function checkHealth(){health.value='checking';try{const r=await fetch('/api/health',{signal:AbortSignal.timeout(6000)});const v=await r.json();health.value=v.executor.available?'online':'offline';}catch{health.value='offline';}}
 async function initialize(){try{saved.value=await readSave();}catch(e){notice.value=String((e as Error).message);}void checkHealth();}
 async function start(name:string){epoch++;clearTimeout(cinemaTimer);Object.assign(s,fresh());s.player_name=name.trim().slice(0,24)||'旅人';ink=new Story(storyJson);ink.variablesState.$('player',s.player_name);practice.value=false;result.value=null;lit.value=[];nextLine();screen.value=s.screen;initAudio(s.settings.muted);sound('drop');await persist();}
 function restore(value:Save){epoch++;replayEpoch++;Object.assign(s,value);ink=new Story(storyJson);ink.state.LoadJson(s.ink_state);screen.value=s.screen;result.value=null;dirty.value=false;busy.value=false;playing.value=false;practice.value=false;lit.value=[];input.value=s.current_task_id===ids[0]?1:s.current_task_id===ids[1]?3:6;initAudio(s.settings.muted);worldAudio(screen.value==='cinematic'||screen.value==='ending');if(screen.value==='cinematic')startCinematic();notice.value='已恢复最近的稳定进度，运行结果需重新验证。';}
 function resume(){if(saved.value)restore(saved.value);}
 async function advance(choice?:number){if(screen.value!=='story')return;if(choice!==undefined)ink.ChooseChoiceIndex(choice);else if(s.choices.length)return;nextLine();screen.value=s.screen;await persist();if(screen.value==='cinematic')startCinematic();}
 function edit(source:string){if(ready.value||source===s.code_drafts[s.current_task_id])return;s.code_drafts[s.current_task_id]=source;epoch++;replayEpoch++;playing.value=false;busy.value=false;result.value=null;dirty.value=true;lit.value=[];scheduleSave();}
 function changeInput(value:string){input.value=value.trim()===''?NaN:Number(value);epoch++;replayEpoch++;playing.value=false;busy.value=false;result.value=null;dirty.value=true;lit.value=[];}
 function hint(){hintOpen.value=true;s.hint_levels[s.current_task_id]=Math.min(3,s.hint_levels[s.current_task_id]+1);log('hint',String(s.hint_levels[s.current_task_id]));scheduleSave();}
 function resetCode(){edit(tasks[s.current_task_id].initial);notice.value='已恢复本关初始代码。';}
 async function replay(stdout:string,n:number){const token=++replayEpoch;playing.value=true;lit.value=[];for(const e of lightEvents(stdout,n)){if(token!==replayEpoch)break;if(e.valid){if(e.duplicate){flash.value=e.id;await new Promise(r=>setTimeout(r,120));flash.value=0;}else lit.value=[...lit.value,e.id];sound('light',e.id);}await new Promise(r=>setTimeout(r,s.settings.reducedMotion?30:300));}if(token===replayEpoch)playing.value=false;}
 async function sha(source:string){const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(source));return [...new Uint8Array(digest)].map(x=>x.toString(16).padStart(2,'0')).join('');}
 async function run(mode:'run'|'submit'){
  if(!s.code_drafts[s.current_task_id].trim()){notice.value='请先输入 C 代码，再运行或提交验证。';return;}
  if(mode==='run'&&taskNumber.value>1&&(!Number.isInteger(input.value)||input.value<0||input.value>6)){notice.value='请在标准输入中填写 0–6 的整数，再运行术式。';return;}
  const source=s.code_drafts[s.current_task_id],id=s.current_task_id,token=++epoch,runInput=mode==='run'?input.value:null;
  result.value=null;dirty.value=false;busy.value=true;lit.value=[];replayEpoch++;playing.value=false;s.submissions++;log('submission',JSON.stringify({mode,source}));await persist();
  try{
   const sourceHash=await sha(source);
   const response=await fetch('/api/submissions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task_id:id,source,mode,input:runInput}),signal:AbortSignal.timeout(10000)});
   if(!response.ok)throw new Error(response.status===429?'执行队列已满，请稍后重试。':'执行服务没有接受提交，请检查服务后重试。');
   let res=await response.json() as Result;const began=Date.now();
   while(['queued','running'].includes(res.status)){
    if(token!==epoch)return;result.value=res;
    if(Date.now()-began>110000)throw new Error('等待执行结果超时。草稿已保留，请重试。');
    await new Promise(r=>setTimeout(r,450));
    const polled=await fetch(`/api/submissions/${res.submission_id}`,{signal:AbortSignal.timeout(8000)});if(!polled.ok)throw new Error('提交记录不可用，请重新运行。');res=await polled.json();
   }
   if(token!==epoch||id!==s.current_task_id||res.source_hash!==sourceHash||source!==s.code_drafts[id])return;
   result.value=res;log('result',JSON.stringify({hash:sourceHash,status:res.status,failed:res.cases.filter(c=>!c.passed).map(c=>c.input)}));
   if(res.passed&&mode==='submit'&&!s.debug&&!practice.value){
    const candidate=clone();if(!candidate.completed_tasks.includes(id))candidate.completed_tasks.push(id);
    if(id===ids[2]&&!candidate.reward_grants.includes('tower-exit-v1'))candidate.reward_grants.push('tower-exit-v1');
    candidate.ink_state=ink.state.ToJson();candidate.save_revision++;candidate.saved_at=new Date().toISOString();
    try{await writeSave(candidate);if(token!==epoch||source!==s.code_drafts[id])return;Object.assign(s,candidate);saved.value=candidate;}catch{notice.value='验证已通过，但进度保存失败。请导出备份后重试提交。';return;}
   }else await persist();
   if(res.status==='service_unavailable')health.value='offline';else health.value='online';
   if(!res.passed)sound('error');
   const show=res.cases.find(c=>!c.passed)??res.cases.at(-1);
   if(show&&res.status!=='compile_error'&&res.status!=='service_unavailable'){if(id!==ids[0])input.value=show.input;await replay(show.stdout,show.input);}
  }catch(e){if(token===epoch){notice.value=(e as Error).message;health.value='offline';result.value={submission_id:'',task_id:id,source_hash:'',status:'service_unavailable',stdout:'',stderr:(e as Error).message,compile_output:'',cases:[],duration_ms:0,passed:false,mode,error_kind:'service_unavailable'};}}
  finally{if(token===epoch)busy.value=false;}
 }
 async function success(){if(!ready.value)return;if(s.current_task_id===ids[2]&&s.first_answer===null){quiz.value=true;return;}await continueSuccess();}
 async function continueSuccess(){quiz.value=false;log('task_exit');if(s.current_task_id===ids[1])s.code_drafts[ids[2]]=s.code_drafts[ids[1]];ink.ChoosePathString(['after_first','after_second','after_third'][taskNumber.value-1]);s.screen='story';nextLine();screen.value=s.screen;result.value=null;await persist();}
 function answer(index:number){if(s.first_answer===null){s.first_answer=index;log('quiz_first',String(index));scheduleSave();}quizFeedback.value=index===1?'正确。i 从 1 开始，1 <= 0 首次检查就不成立，所以没有输出。':'再想一想：没有灯的回路，不应该收到任何点灯命令。';}
 function startCinematic(){worldAudio(true);sound('door');lit.value=[1,2,3,4,5,6];clearTimeout(cinemaTimer);cinemaTimer=setTimeout(()=>void finishCinematic(),s.settings.reducedMotion?1000:26000);}
 async function finishCinematic(){clearTimeout(cinemaTimer);if(screen.value!=='cinematic')return;ink.ChoosePathString('echo');s.screen='story';nextLine();screen.value=s.screen;await persist();}
 function toggleMute(){s.settings.muted=!s.settings.muted;mute(s.settings.muted);if(screen.value!=='title')scheduleSave();}
 async function goTitle(){epoch++;replayEpoch++;busy.value=false;playing.value=false;clearTimeout(cinemaTimer);await persist();screen.value='title';worldAudio(false);}
 async function exportSave(){s.ink_state=ink.state.ToJson();const data=screen.value==='title'&&saved.value?saved.value:clone();const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='旧塔的最后一盏灯_存档.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);notice.value='存档已导出。';}
 async function importSave(file:File){try{if(file.size>2000000)throw new Error('存档文件过大。');const parsed=validateSave(JSON.parse(await file.text()));await writeSave(parsed);saved.value=parsed;restore(parsed);settingsOpen.value=false;notice.value='存档导入成功。';}catch(e){notice.value=(e as Error).message;}}
 function experiment(){practice.value=true;s.current_task_id=ids[2];screen.value='task';input.value=6;result.value=null;lit.value=[];notice.value='自由实验不会重复发奖。退出实验即可回到结算。';}
 function endExperiment(){practice.value=false;screen.value='ending';}
 async function debugJump(target:'task'|'cinematic'|'ending'='task'){if(!import.meta.env.DEV||!new URLSearchParams(location.search).has('debug'))return;epoch++;replayEpoch++;clearTimeout(cinemaTimer);clearTimeout(saveTimer);Object.assign(s,fresh());s.debug=true;s.player_name='调试旅人';ink=new Story(storyJson);s.current_task_id=ids[2];s.screen=target;screen.value=target;input.value=6;result.value=null;lit.value=[];busy.value=false;practice.value=false;initAudio(s.settings.muted);if(target==='cinematic')startCinematic();await persist();}
 const timer=setInterval(()=>{if(!document.hidden&&screen.value!=='title'){s.active_seconds++;if(s.active_seconds%10===0)void persist();}},1000);
 function visibility(){if(document.hidden){suspendAudio();if(screen.value!=='title')void persist();}else resumeAudio();}
 document.addEventListener('visibilitychange',visibility);
 function dispose(){clearInterval(timer);clearTimeout(cinemaTimer);clearTimeout(saveTimer);epoch++;document.removeEventListener('visibilitychange',visibility);}
 return {s,screen,saved,notice,busy,result,dirty,lit,flash,playing,input,health,settingsOpen,hintOpen,quiz,quizFeedback,practice,task,taskNumber,lightCount,ready,initialize,start,resume,advance,edit,changeInput,hint,resetCode,run,success,continueSuccess,answer,finishCinematic,toggleMute,persist,goTitle,exportSave,importSave,experiment,endExperiment,checkHealth,debugJump,dispose,replay};
}
