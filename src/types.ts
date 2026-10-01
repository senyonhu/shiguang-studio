export type TaskId = 'tower_light_01' | 'tower_light_02' | 'tower_light_03';
export type Screen = 'title' | 'story' | 'task' | 'cinematic' | 'ending';
export type Status = 'queued' | 'running' | 'passed' | 'wrong_answer' | 'compile_error' | 'runtime_error' | 'timeout' | 'output_limit' | 'service_unavailable';
export interface CaseResult {input:number; expected:string; stdout:string; stderr:string; compile_output:string; status:Status; passed:boolean; duration_ms:number}
export interface Result {submission_id:string; task_id:TaskId; source_hash:string; status:Status; stdout:string; stderr:string; compile_output:string; cases:CaseResult[]; duration_ms:number; passed:boolean; mode:'run'|'submit'; error_kind:string|null}
export interface Save {
 schema_version:1; content_version:'0.1.0'; save_revision:number; saved_at:string; player_name:string; ink_state:string;
 screen:Screen; current_task_id:TaskId; code_drafts:Record<TaskId,string>; completed_tasks:TaskId[]; reward_grants:string[];
 settings:{muted:boolean; fontSize:number; reducedMotion:boolean}; line:string; choices:{text:string; index:number}[]; face:string;
 hint_levels:Record<TaskId,number>; submissions:number; active_seconds:number; first_answer:number|null; debug:boolean;
 records:{at:string; event:string; task:TaskId; detail:string}[];
}
