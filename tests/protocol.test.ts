import { describe, it, expect } from 'vitest';
import { lightEvents } from '../src/tasks';
import { fresh, validateSave } from '../src/save';
import { Story } from 'inkjs';
import storyJson from '../src/generated/story.json';
describe('stdout replay',()=>{
 it('preserves duplicates and invalid tokens',()=>{expect(lightEvents('1\n2\n2\n0\nhello\n',3).map(e=>[e.id,e.valid,e.duplicate])).toEqual([[1,true,false],[2,true,false],[2,true,true],[0,false,false],[0,false,false]]);});
 it('does not light anything for empty input',()=>{expect(lightEvents('',0)).toEqual([]);});
 it('does not interpret padded or blank lines as commands',()=>{expect(lightEvents(' 1\n\n+1\n01\n',1).every(e=>!e.valid)).toBe(true);});
});
describe('save validation',()=>{
 function valid(){const save=fresh();save.ink_state=new Story(storyJson).state.ToJson();return save;}
 it('round trips a real Ink runtime snapshot',()=>expect(validateSave(valid()).schema_version).toBe(1));
 it.each(['content_version','ink_state','code_drafts','settings','records','hint_levels'])('rejects damaged %s without mutating the original',key=>{const save=valid();const bad=JSON.parse(JSON.stringify(save));bad[key]=null;expect(()=>validateSave(bad)).toThrow();expect(save.schema_version).toBe(1);});
 it('rejects inconsistent reward states',()=>{const save=valid();save.reward_grants=['tower-exit-v1'];expect(()=>validateSave(save)).toThrow();});
});
describe('Ink progression',()=>{
 it('both branches reach the first task and state can resume',()=>{for(const choice of [0,1]){const story=new Story(storyJson);while(story.canContinue)story.Continue();expect(story.currentChoices).toHaveLength(2);story.ChooseChoiceIndex(choice);let tags:string[]=[];while(story.canContinue){story.Continue();tags.push(...story.currentTags??[]);}expect(tags).toContain('task:tower_light_01');const restored=new Story(storyJson);restored.state.LoadJson(story.state.ToJson());restored.ChoosePathString('after_first');expect(restored.Continue()).toContain('亮了');}});
});
