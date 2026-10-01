"""Render the existing original procedural score to offline WAV files."""
from array import array
import math
from pathlib import Path
import random
import wave

DEST = Path(__file__).resolve().parents[1] / 'client/game/audio'
DEST.mkdir(parents=True,exist_ok=True)
RATE=22050

def render(name,duration,notes,noise=0):
    samples=[0.0]*int(duration*RATE)
    rng=random.Random(29)
    for start,hz,length,gain in notes:
        offset=int(start*RATE)
        for j in range(min(int(length*RATE),len(samples)-offset)):
            t=j/RATE
            env=min(1,t/.04)*math.exp(-t*4/max(length,.1))*min(1,(length-t)/.12)
            samples[offset+j]+=gain*env*(math.sin(t*hz*math.tau)+.2*math.sin(t*hz*2*math.tau))
    output=array('h')
    for i,sample in enumerate(samples):
        t=i/RATE
        sample+=noise*(rng.random()*2-1)*math.sin(math.pi*t/duration)**2
        edge=min(1,t/.12,(duration-t)/.3)
        output.append(int(max(-1,min(1,sample*edge))*32760))
    with wave.open(str(DEST/(name+'.wav')),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(RATE);f.writeframes(output.tobytes())

for name,scale in [('tower',[146.83,220,293.66,220,174.61,220,261.63,196]),('world',[220,277.18,329.63,440,369.99,329.63,277.18,329.63])]:
    notes=[]
    for i in range(24):
        notes.append((i*1.05,scale[i%8],2.6,.065))
        if i%4==0: notes.append((i*1.05,scale[i%8]/2,3.8,.04))
    render(name,28,notes)
render('lamp',.9,[(0,523.25,.8,.18),(0,1046.5,.5,.04)])
render('feedback',1.5,[(0,523.25,.9,.12),(.15,659.25,.9,.12),(.3,783.99,1.1,.12)])
render('tick',.45,[(0,174.61,.4,.09)])
render('drip',1.2,[(0,1200,.3,.08),(.5,980,.4,.035)])
render('door',3,[(0,45,3,.16),(0,90,2,.05)],noise=.03)
render('echo',2.5,[(0,220,2.5,.09),(.3,233.08,2.0,.06),(.6,440,1.8,.03)])
print('Rendered 2 original score loops and 6 short effects.')
