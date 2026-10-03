// Development-only observation of real input around one checkpoint action.
// This adds event listeners only; it never modifies the game state or results.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';

const args = process.argv.slice(2);
const option = name => args[args.indexOf(name) + 1];
const port = Number(option('--port'));
const output = path.resolve(option('--output'));
const workspace = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
if (!Number.isInteger(port) || port < 1024 || port > 65535 || !output.startsWith(workspace + path.sep)) throw new Error('Invalid QA port/output');
await fs.mkdir(output, {recursive:true});
const report = {scope:'actual development CDP input observation, no game-state writes', inputs:[], transitions:[], screenshots:[], status:'failed'};
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let ws, next = 1, closed = false;
const requests = new Map();
function call(method, params={}) {
  return new Promise((resolve,reject) => {
    const id = next++;
    const timer = setTimeout(() => {requests.delete(id); reject(new Error('CDP timeout '+method));},5000);
    requests.set(id,{resolve,reject,timer});
    ws.send(JSON.stringify({id,method,params}));
  });
}
async function screenshot(name) {
  const result = await call('Page.captureScreenshot',{format:'png'});
  const bytes = Buffer.from(result.data,'base64');
  const filename = path.join(output,name+'.png');
  await fs.writeFile(filename,bytes);
  report.screenshots.push({path:filename,sha256:crypto.createHash('sha256').update(bytes).digest('hex')});
}
try {
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const target = targets.find(item => item.type==='page' && item.url.startsWith('https://minigame-prod_checkpoint.invalid/'));
  if (!target) throw new Error('The actual checkpoint page is unavailable');
  report.target={id:target.id,url:target.url,title:target.title};
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject) => {ws.addEventListener('open',resolve,{once:true});ws.addEventListener('error',reject,{once:true});});
  ws.addEventListener('close',() => {closed=true; for(const r of requests.values()){clearTimeout(r.timer);r.reject(new Error('Actual host closed'));} requests.clear();});
  ws.addEventListener('message',event => {
    const message=JSON.parse(event.data);
    if(message.method==='Runtime.consoleAPICalled' && message.params.args?.[0]?.value==='qa-checkpoint-input') {
      report.inputs.push(JSON.parse(message.params.args[1].value));
    }
    const request=requests.get(message.id);
    if(request){clearTimeout(request.timer);requests.delete(message.id);message.error?request.reject(new Error(JSON.stringify(message.error))):request.resolve(message.result);}
  });
  await call('Runtime.enable');
  await call('Page.enable');
  await call('Runtime.evaluate',{expression:`(() => {
    for (const type of ['pointerdown','pointerup','click','keydown','keyup']) document.addEventListener(type,e => {
      const target=e.target.closest?.('button') || e.target;
      console.debug('qa-checkpoint-input',JSON.stringify({type:e.type,time:performance.now(),trusted:e.isTrusted,
        x:e.clientX,y:e.clientY,button:e.button,key:e.key,code:e.code,id:target.id,testid:target.dataset?.testid,
        part:target.dataset?.part,wire:target.dataset?.wire,stage:document.querySelector('#workshop')?.dataset.stage}));
    },true);
  })()`});
  await screenshot('observed_before_first_input');
  const result=await call('Runtime.evaluate',{expression:`(() => {const e=document.querySelector('[data-part=temperature]');if(!e||e.disabled)throw Error('Sensor unavailable'); const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,stage:document.querySelector('#workshop').dataset.stage};})()`,returnByValue:true});
  if(result.exceptionDetails)throw new Error('Sensor observation failed');
  const observed=result.result.value;
  report.actual_click={selector:'[data-part=temperature]',...observed};
  await call('Input.dispatchMouseEvent',{type:'mouseMoved',x:observed.x,y:observed.y});
  await call('Input.dispatchMouseEvent',{type:'mousePressed',x:observed.x,y:observed.y,button:'left',clickCount:1});
  await call('Input.dispatchMouseEvent',{type:'mouseReleased',x:observed.x,y:observed.y,button:'left',clickCount:1});
  const deadline=Date.now()+4500;
  while(!closed && Date.now()<deadline){
    try{
      const value=await call('Runtime.evaluate',{expression:"document.querySelector('#workshop').dataset.stage",returnByValue:true});
      if(report.transitions.at(-1)?.stage!==value.result.value) report.transitions.push({time:Date.now(),stage:value.result.value});
    }catch(error){if(!closed)throw error;}
    await delay(100);
  }
  report.status='observed';
}catch(error){report.error={message:error.message,stack:error.stack};}
finally{
  ws?.close();
  report.tool_sha256=crypto.createHash('sha256').update(await fs.readFile(fileURLToPath(import.meta.url))).digest('hex');
  await fs.writeFile(path.join(output,'report.json'),JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify({status:report.status,report:path.join(output,'report.json')}));
  if(report.status!=='observed')process.exitCode=1;
}
