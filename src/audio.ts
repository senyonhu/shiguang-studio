// Original procedural score and effects; no remote audio or licensed samples.
let ctx:AudioContext|undefined, master:GainNode, score:ReturnType<typeof setInterval>|undefined, step=0;
let outside=false;
export function initAudio(muted:boolean){if(!ctx){ctx=new AudioContext();master=ctx.createGain();master.connect(ctx.destination);score=setInterval(()=>{if(!ctx||ctx.state!=='running')return;const notes=outside?[220,277.18,329.63,440,369.99,329.63,277.18,329.63]:[146.83,220,293.66,220,174.61,220,261.63,196];tone(notes[step++%notes.length],2.6,.028,'sine');if(step%4===0)tone(notes[(step-1)%notes.length]/2,3.8,.018,'triangle');},1050);}void ctx.resume();mute(muted);}
export function mute(value:boolean){if(ctx)master.gain.setTargetAtTime(value?0:1,ctx.currentTime,.08);}
export function worldAudio(value:boolean){outside=value;}
function tone(hz:number,duration:number,volume:number,type:OscillatorType='sine'){if(!ctx)return;const o=ctx.createOscillator(),g=ctx.createGain();o.type=type;o.frequency.value=hz;g.gain.setValueAtTime(0,ctx.currentTime);g.gain.linearRampToValueAtTime(volume,ctx.currentTime+.025);g.gain.exponentialRampToValueAtTime(.0001,ctx.currentTime+duration);o.connect(g);g.connect(master);o.start();o.stop(ctx.currentTime+duration+.05);}
export function sound(name:'light'|'error'|'door'|'drop',id=1){if(name==='light'){tone(440*2**(id/12),.8,.075);tone(880*2**(id/12),.5,.02);}if(name==='error')tone(130,.35,.04,'triangle');if(name==='door'){tone(45,3,.10,'sawtooth');tone(90,2,.03);}if(name==='drop')tone(1200,.3,.018);}
export function suspendAudio(){void ctx?.suspend();}
export function resumeAudio(){void ctx?.resume();}
