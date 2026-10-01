import { openDB } from 'idb';
import { Story } from 'inkjs';
import storyJson from './generated/story.json';
import { ids, first, loop } from './tasks';
import type { Save } from './types';
const db = () => openDB('last-light',1,{upgrade(db){db.createObjectStore('saves');}});
export function fresh():Save {return {schema_version:1,content_version:'0.1.0',save_revision:0,saved_at:new Date().toISOString(),player_name:'旅人',ink_state:'',screen:'story',current_task_id:ids[0],code_drafts:{tower_light_01:first,tower_light_02:loop,tower_light_03:loop},completed_tasks:[],reward_grants:[],settings:{muted:false,fontSize:15,reducedMotion:false},line:'',choices:[],face:'worried',hint_levels:{tower_light_01:0,tower_light_02:0,tower_light_03:0},submissions:0,active_seconds:0,first_answer:null,debug:false,records:[]};}
export function validateSave(raw:unknown):Save {
 const s=raw as Save;
 const fail=()=>{throw new Error('存档格式、版本或内容不兼容。当前存档未被覆盖。');};
 if(!s||typeof s!=='object'||s.schema_version!==1||s.content_version!=='0.1.0') return fail();
 if(typeof s.player_name!=='string'||s.player_name.length>24||typeof s.ink_state!=='string'||s.ink_state.length>500000||!ids.includes(s.current_task_id)||!['story','task','cinematic','ending'].includes(s.screen)) return fail();
 if(!s.code_drafts||ids.some(id=>typeof s.code_drafts[id]!=='string'||s.code_drafts[id].length>16000))return fail();
 if(!Array.isArray(s.completed_tasks)||s.completed_tasks.some(id=>!ids.includes(id))||new Set(s.completed_tasks).size!==s.completed_tasks.length)return fail();
 if(!Array.isArray(s.reward_grants)||s.reward_grants.some(id=>id!=='tower-exit-v1')||s.reward_grants.length>1)return fail();
 if(s.completed_tasks.includes(ids[2])&&!s.completed_tasks.includes(ids[1])||s.completed_tasks.includes(ids[1])&&!s.completed_tasks.includes(ids[0]))return fail();
 if(s.reward_grants.length&&!s.completed_tasks.includes(ids[2]))return fail();
 if(!s.settings||typeof s.settings.muted!=='boolean'||typeof s.settings.reducedMotion!=='boolean'||![13,15,17,19].includes(s.settings.fontSize))return fail();
 if(!s.hint_levels||ids.some(id=>!Number.isInteger(s.hint_levels[id])||s.hint_levels[id]<0||s.hint_levels[id]>3))return fail();
 if([s.save_revision,s.submissions,s.active_seconds].some(x=>!Number.isFinite(x)||x<0)||typeof s.debug!=='boolean'||![null,0,1,2].includes(s.first_answer))return fail();
 if(typeof s.saved_at!=='string'||!Number.isFinite(Date.parse(s.saved_at))||typeof s.line!=='string'||s.line.length>2000||!['worried','thinking','relieved'].includes(s.face))return fail();
 if(!Array.isArray(s.choices)||s.choices.length>4||s.choices.some(c=>typeof c.text!=='string'||c.text.length>200||!Number.isInteger(c.index)))return fail();
 if(!Array.isArray(s.records)||s.records.length>2000||s.records.some(r=>!r||typeof r.at!=='string'||typeof r.event!=='string'||!ids.includes(r.task)||typeof r.detail!=='string'||r.detail.length>20000))return fail();
 try {const story=new Story(storyJson); story.state.LoadJson(s.ink_state); if(JSON.stringify(story.currentChoices.map(c=>({text:c.text,index:c.index})))!==JSON.stringify(s.choices))return fail();}catch{return fail();}
 return structuredClone(s);
}
export async function readSave(){const value=await(await db()).get('saves','main');return value?validateSave(value):null;}
let pending=Promise.resolve();
export function writeSave(save:Save){const snapshot=JSON.parse(JSON.stringify(save)); pending=pending.catch(()=>{}).then(async()=>{await(await db()).put('saves',snapshot,snapshot.debug?'debug':'main');});return pending;}
export async function deleteSave(){await pending.catch(()=>{});await(await db()).delete('saves','main');}
