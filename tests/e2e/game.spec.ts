import { test, expect, type Page } from '@playwright/test';
async function start(page:Page){await page.goto('/');await page.getByRole('button',{name:'开始旅程',exact:true}).click();await page.getByRole('textbox',{name:'旅人昵称'}).fill('测试旅人');await page.getByRole('button',{name:'走进旧塔'}).click();}
async function toTask(page:Page){for(let i=0;i<12;i++){if(await page.getByRole('heading',{name:/第一束光|为什么最后一盏不亮|点亮整座塔/}).isVisible().catch(()=>false))return;const choice=page.getByRole('button',{name:/这是哪里/});if(await choice.isVisible())await choice.click();else await page.locator('.next-dialogue').click();}throw new Error('Task not reached');}
async function readSave(page:Page){return page.evaluate(async()=>{const db=await new Promise<IDBDatabase>((resolve,reject)=>{const req=indexedDB.open('last-light',1);req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});return await new Promise<any>((resolve,reject)=>{const req=db.transaction('saves').objectStore('saves').get('main');req.onsuccess=()=>resolve(req.result);req.onerror=()=>reject(req.error);});});}
test('title, story choices, editor, reload, settings and 1366x768 layout',async({page})=>{
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('/');await page.screenshot({path:'.runtime/title-1366.png'});
 await start(page);await toTask(page);
 await expect(page.getByRole('textbox',{name:'printf 输出内容'})).toHaveValue('0');
 await page.getByRole('textbox',{name:'printf 输出内容'}).fill('1');
 await expect.poll(async()=>(await readSave(page)).code_drafts.tower_light_01).toContain('printf("1\\n")');
 await page.screenshot({path:'.runtime/task-1366.png'});
 const button=await page.getByRole('button',{name:'提交验证',exact:true}).boundingBox();expect(button!.y+button!.height).toBeLessThan(768);
 await page.setViewportSize({width:1920,height:1080});await page.screenshot({path:'.runtime/task-1920.png'});const large=await page.getByRole('button',{name:'提交验证',exact:true}).boundingBox();expect(large!.y+large!.height).toBeLessThan(1080);await page.setViewportSize({width:1366,height:768});
 await page.reload();await page.getByRole('button',{name:/继续旅程/}).click();await expect(page.getByRole('textbox',{name:'printf 输出内容'})).toHaveValue('1');
 await page.getByRole('button',{name:'查看提示',exact:true}).click();await expect(page.getByText('灯上的编号是多少？')).toBeVisible();
 await page.getByRole('button',{name:'关闭提示'}).click();await page.getByRole('button',{name:'恢复初始代码',exact:true}).click();await page.getByRole('button',{name:'保留草稿'}).click();await expect(page.getByRole('textbox',{name:'printf 输出内容'})).toHaveValue('1');
 await page.getByRole('button',{name:'设置与存档',exact:true}).click();await expect(page.getByRole('dialog')).toBeVisible();await page.getByRole('button',{name:'关闭设置'}).click();expect(errors).toEqual([]);
});
test('first task supports direct code deletion, insertion, undo and reload',async({page})=>{
 await start(page);await toTask(page);
 const editor=page.getByRole('textbox',{name:'C 语言代码编辑器',exact:true});
 await editor.press('ControlOrMeta+a');await editor.press('Backspace');
 await expect.poll(async()=>(await readSave(page)).code_drafts.tower_light_01).toBe('');
 await expect(page.getByRole('textbox',{name:'printf 输出内容'})).toHaveCount(0);
 let submissions=0;page.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/api/submissions'))submissions++;});
 await page.getByRole('button',{name:'运行术式',exact:true}).click();await expect(page.getByRole('status')).toContainText('请先输入 C 代码');expect(submissions).toBe(0);
 const source='#include <stdio.h>\nint main(void) { puts("1"); return 0; }';
 await editor.focus();await page.keyboard.insertText(source);
 await expect.poll(async()=>(await readSave(page)).code_drafts.tower_light_01).toBe(source);
 await editor.press('ControlOrMeta+Home');await page.keyboard.insertText(' ');
 await expect.poll(async()=>(await readSave(page)).code_drafts.tower_light_01).toBe(' '+source);
 await editor.press('ControlOrMeta+z');await expect.poll(async()=>(await readSave(page)).code_drafts.tower_light_01).toBe(source);
 await page.reload();await page.getByRole('button',{name:/继续旅程/}).click();await expect(editor).toBeEditable();expect((await readSave(page)).code_drafts.tower_light_01).toBe(source);
 const submitted=page.waitForRequest(r=>r.method()==='POST'&&r.url().endsWith('/api/submissions'));
 await page.route('**/api/submissions',r=>r.abort('failed'));
 await page.getByRole('button',{name:'运行术式',exact:true}).click();expect((await submitted).postDataJSON().source).toBe(source);
});
test('late real execution result cannot overwrite an edited draft',async({page,request})=>{
 const health=await request.get('http://127.0.0.1:8000/api/health');test.skip(!(await health.json()).executor.available,'Real Judge0 not ready');
 await start(page);await toTask(page);await page.getByRole('textbox',{name:'printf 输出内容'}).fill('1');
 const submitted=page.waitForResponse(r=>r.url().endsWith('/api/submissions')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'提交验证',exact:true}).click();await page.getByRole('textbox',{name:'printf 输出内容'}).fill('0');
 const response=await submitted;const initial=await response.json();
 await expect.poll(async()=>{const r=await request.get('http://127.0.0.1:8000/api/submissions/'+initial.submission_id);return (await r.json()).status;},{timeout:30000}).toBe('passed');
 await expect(page.getByText('代码已修改 · 待重新验证')).toBeVisible();expect((await readSave(page)).completed_tasks).toEqual([]);await expect(page.getByRole('textbox',{name:'printf 输出内容'})).toHaveValue('0');await expect(page.getByRole('button',{name:'继续前行'})).toHaveCount(0);
});
test('full cinematic and skip reach the same Ink node; debug preserves normal saves',async({page})=>{
 await page.clock.install();await page.goto('/?debug=1');await page.getByRole('button',{name:'设置与存档'}).click();await page.getByRole('button',{name:/开发调试：跳转灯阵/}).click();await page.getByRole('button',{name:'出口',exact:true}).click();
 await page.clock.fastForward(27000);await expect(page.locator('.speaker>span')).toHaveText('未知残响');const full=await page.locator('.dialogue-box>p').innerText();
 await page.getByRole('button',{name:'出口',exact:true}).click();await page.getByRole('button',{name:'跳过演出'}).click();await expect(page.locator('.dialogue-box>p')).toHaveText(full);expect(await readSave(page)).toBeUndefined();
});
test('unavailable execution preserves code and does not advance',async({page})=>{
 await page.route('**/api/submissions',route=>route.abort('failed'));
 await start(page);await toTask(page);await page.getByRole('textbox',{name:'printf 输出内容'}).fill('1');await page.getByRole('button',{name:'提交验证',exact:true}).click();await expect(page.getByText('执行服务暂不可用',{exact:true})).toBeVisible();expect((await readSave(page)).completed_tasks).toEqual([]);await expect(page.getByRole('textbox',{name:'printf 输出内容'})).toHaveValue('1');
});
test('invalid import does not overwrite valid checkpoint',async({page})=>{
 await start(page);await toTask(page);const before=await readSave(page);
 await page.locator('input[type=file]').setInputFiles({name:'broken.json',mimeType:'application/json',buffer:Buffer.from('{"schema_version":999}')});
 await expect(page.getByRole('status')).toContainText('不兼容');const after=await readSave(page);expect(after.player_name).toBe(before.player_name);expect(after.ink_state).toBe(before.ink_state);
});
test('complete real-C route, four cases, reward deduplication and ending',async({page,request})=>{
 let available=false;for(let i=0;i<12;i++){const health=await request.get('http://127.0.0.1:8000/api/health');available=(await health.json()).executor.available;if(available)break;await new Promise(resolve=>setTimeout(resolve,2000));}test.skip(!available,'Real Judge0 not ready');
 await start(page);await toTask(page);
 await page.getByRole('button',{name:'运行术式',exact:true}).click();await expect(page.getByText('收到了编号 0，当前灯的编号是 1。')).toBeVisible();
 await page.getByRole('textbox',{name:'printf 输出内容'}).fill('1');await page.getByRole('button',{name:'提交验证',exact:true}).click();await expect(page.getByRole('button',{name:'继续前行'})).toBeEnabled();await page.getByRole('button',{name:'继续前行'}).click();await toTask(page);
 await page.getByRole('button',{name:'运行术式',exact:true}).click();await expect(page.locator('.stdout pre')).toHaveText('1\n2');await expect(page.locator('.light-position.lit')).toHaveCount(2);
 const editor=page.getByRole('textbox',{name:'C 语言代码编辑器',exact:true});
 const stdin=page.getByRole('spinbutton',{name:'试运行灯数'});
 const run=page.getByRole('button',{name:'运行术式',exact:true});
 await expect(stdin).toBeEnabled();await expect(stdin).toHaveValue('3');
 for(const invalid of ['', '7', '1.5']){await stdin.fill(invalid);await run.click();await expect(page.getByRole('status')).toContainText('请在标准输入中填写 0–6 的整数');}
 const second='#include <stdio.h>\nint main(void) { int n; scanf("%d", &n); for(int i=1;i<=n;i++) printf("%d\\n",i); return 0; }';
 await editor.press('ControlOrMeta+a');await editor.press('Backspace');await expect.poll(async()=>(await readSave(page)).code_drafts.tower_light_02).toBe('');
 await page.keyboard.insertText(second);await expect.poll(async()=>(await readSave(page)).code_drafts.tower_light_02).toBe(second);
 await page.reload();await page.getByRole('button',{name:/继续旅程/}).click();expect((await readSave(page)).code_drafts.tower_light_02).toBe(second);
 for(const n of [0,4]){await stdin.fill(String(n));await run.click();await expect(page.locator('.stdout pre')).toHaveText(n===0?'（空输出）':'1\n2\n3\n4');await expect(run).toBeEnabled();await expect(page.locator('.light-position.lit')).toHaveCount(n);}
 await stdin.fill('6');await expect(page.locator('.stdout')).toHaveCount(0);await page.screenshot({path:'.runtime/task2-input.png'});
 await page.getByRole('button',{name:'提交验证',exact:true}).click();await expect(page.getByRole('button',{name:'继续前行'})).toBeEnabled();await expect(stdin).toHaveValue('3');await page.getByRole('button',{name:'继续前行'}).click();await toTask(page);
 const third='#include <stdio.h>\nint main(void) { int n; scanf("%d", &n); int i=1; while(i<=n) { printf("%d\\n",i); i++; } return 0; }';
 await editor.press('ControlOrMeta+a');await editor.press('Backspace');await expect.poll(async()=>(await readSave(page)).code_drafts.tower_light_03).toBe('');
 await page.keyboard.insertText(third);await expect.poll(async()=>(await readSave(page)).code_drafts.tower_light_03).toBe(third);
 await page.reload();await page.getByRole('button',{name:/继续旅程/}).click();expect((await readSave(page)).code_drafts.tower_light_03).toBe(third);
 for(const n of [0,6]){await stdin.fill(String(n));await run.click();await expect(page.locator('.stdout pre')).toHaveText(n===0?'（空输出）':'1\n2\n3\n4\n5\n6');await expect(run).toBeEnabled();await expect(page.locator('.light-position.lit')).toHaveCount(n);}
 await stdin.fill('2');await page.screenshot({path:'.runtime/task3-input.png'});
 await page.getByRole('button',{name:'验证全部回路'}).click();await expect(page.getByRole('button',{name:'打开塔门'})).toBeEnabled({timeout:60000});await expect(page.locator('.case-list .pass-text')).toHaveCount(4);await page.screenshot({path:'.runtime/all-cases.png'});
 await page.reload();await page.getByRole('button',{name:/继续旅程/}).click();await page.getByRole('button',{name:'打开塔门'}).click();await page.getByRole('button',{name:/第一次检查条件/}).click();await page.getByRole('button',{name:'我明白了，出发'}).click();
 for(let i=0;i<4;i++){if(await page.getByRole('button',{name:'跳过演出'}).isVisible())break;await page.locator('.next-dialogue').click();}
 await page.getByRole('button',{name:'跳过演出'}).click();await expect(page.locator('.speaker>span')).toHaveText('未知残响');
 for(let i=0;i<5;i++){if(await page.getByRole('heading',{name:'你为旧塔，带来了光。'}).isVisible())break;await page.locator('.next-dialogue').click();}
 await expect(page.getByRole('heading',{name:'你为旧塔，带来了光。'})).toBeVisible();await page.screenshot({path:'.runtime/ending.png'});
 const save=await readSave(page);expect(save.completed_tasks).toHaveLength(3);expect(save.reward_grants).toEqual(['tower-exit-v1']);expect(save.first_answer).toBe(1);
 await page.getByRole('button',{name:'继续实验',exact:true}).click();await page.getByRole('button',{name:'验证全部回路'}).click();await expect(page.locator('.case-list .pass-text')).toHaveCount(4,{timeout:60000});expect((await readSave(page)).reward_grants).toEqual(['tower-exit-v1']);
});
