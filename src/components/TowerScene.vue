<script setup lang="ts">
import { computed } from 'vue';
import Character from './Character.vue';
const props=defineProps<{lit:number[];count:number;face:string;flash:number;mode:string;outside:boolean;showCharacter:boolean;reducedMotion:boolean}>();
const slots=computed(()=>Array.from({length:props.count},(_,i)=>i+1));
</script>
<template>
<div class="tower-scene" :class="[mode,{outside,'reduced-motion':reducedMotion,'has-light':lit.length>0}]">
 <div class="interior-art"><svg viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
 <defs>
  <linearGradient id="stone" x2="0" y2="1"><stop stop-color="#152a39"/><stop offset="1" stop-color="#09151e"/></linearGradient>
  <radialGradient id="mist"><stop stop-color="#4e7f8d" stop-opacity=".3"/><stop offset="1" stop-color="#152735" stop-opacity="0"/></radialGradient>
  <linearGradient id="moonbeam" x2=".7" y2="1"><stop stop-color="#a1c4ce" stop-opacity=".18"/><stop offset="1" stop-color="#99bccd" stop-opacity="0"/></linearGradient>
  <pattern id="blocks" width="240" height="140" patternUnits="userSpaceOnUse"><path d="M0 0H240V140H0ZM0 70H240M120 0V70M60 70V140" fill="none" stroke="#527185" stroke-opacity=".09" stroke-width="2"/></pattern>
  <linearGradient id="floor" x2="0" y2="1"><stop stop-color="#233946"/><stop offset="1" stop-color="#080f16"/></linearGradient>
  <linearGradient id="goldbeam" x2="0" y2="1"><stop stop-color="#efc880" stop-opacity=".55"/><stop offset="1" stop-color="#e9bf7d" stop-opacity="0"/></linearGradient>
 </defs>
 <rect width="1600" height="1000" fill="url(#stone)"/><rect width="1600" height="850" fill="url(#blocks)"/>
 <path d="M210 0V735H360V270Q800 -130 1240 270V735H1390V0Z" fill="#09121a"/><path d="M300 0V750M1300 0V750" stroke="#3d5869" stroke-opacity=".22" stroke-width="13"/>
 <path d="M410 760V355Q800 -30 1190 355V760" fill="none" stroke="#344d60" stroke-width="45"/><path d="M420 760V355Q800 0 1180 355V760" fill="none" stroke="#628396" stroke-opacity=".24" stroke-width="3"/>
 <path d="M560 755V360Q800 115 1040 360V755" fill="#0a1720" stroke="#354f60" stroke-width="12"/><path d="M580 755V365Q800 150 1020 365V755" fill="none" stroke="#5e7684" stroke-opacity=".22" stroke-width="3"/>
 <path d="M800 232V742" stroke="#a6c1be" stroke-opacity=".16" stroke-width="2"/>
 <g fill="none" stroke="#78a3a8" stroke-opacity=".19"><circle cx="800" cy="520" r="130"/><circle cx="800" cy="520" r="105"/><path d="M800 365L935 595H665ZM800 675L665 445H935Z"/><path d="M650 520H950M800 370V670"/><circle cx="800" cy="520" r="48"/></g>
 <path d="M770 0H845V130H770Z" fill="#7897a2" opacity=".14"/><path d="M770 40L350 950H1120L845 40" fill="url(#moonbeam)"/>
 <path d="M0 800L470 710H1130L1600 800V1000H0Z" fill="url(#floor)"/>
 <g stroke="#6c8993" opacity=".12"><path d="M0 880H1600M0 965H1600M170 1000L570 720M1430 1000L1030 720M550 1000L720 720M1050 1000L880 720"/></g>
 <ellipse cx="760" cy="815" rx="380" ry="75" fill="none" stroke="#6eaba9" stroke-opacity=".11" stroke-width="2"/><ellipse cx="760" cy="815" rx="310" ry="55" fill="none" stroke="#6eaba9" stroke-opacity=".08"/>
 <g fill="#11202b" stroke="#2b4353" stroke-width="2"><path d="M78 0H195L220 805H42Z"/><path d="M1405 0H1522L1558 805H1380Z"/></g>
 <g fill="#1e3341" stroke="#3b5260" stroke-opacity=".3"><path d="M33 735H224V782H33ZM28 782H230V814H28ZM1376 735H1567V782H1376ZM1370 782H1573V814H1370Z"/><path d="M40 120H211V155H40ZM1389 120H1560V155H1389Z"/></g>
 <path d="M293 10L302 82L284 116L306 151M1151 244L1110 301L1135 320L1102 383M192 574L240 600L229 626" fill="none" stroke="#050e15" stroke-width="4"/>
 <g fill="#263c48"><path d="M1220 801L1260 775L1310 801Z"/><path d="M1190 830L1224 805L1255 835Z"/><path d="M332 812L370 790L401 817Z"/></g>
 <ellipse cx="700" cy="500" rx="680" ry="460" fill="url(#mist)"/>
 <path v-if="lit.length===6" d="M793 245H807V740L1240 1000H300L793 740Z" fill="url(#goldbeam)"/>
 </svg></div>
 <div class="world-art"><div class="world-sky"></div><div class="sun"></div><svg class="mountains far" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice"><path d="M0 610L180 280L330 500L530 240L720 520L940 300L1070 470L1300 240L1600 540V900H0" fill="#80969e"/><path d="M530 240L425 400L545 360L600 390ZM1300 240L1203 379L1290 346L1350 370Z" fill="#ced3c9" opacity=".8"/><path d="M0 780L330 490L670 770L990 560L1200 690L1600 470V900H0" fill="#496c7c"/></svg><svg class="mountains middle" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice"><g fill="#527585"><path d="M770 450L925 410L1080 440L1010 470L930 595L872 477Z"/><path d="M220 370L350 342L440 360L357 445L330 510L290 411Z"/></g><g fill="#b7b9aa"><path d="M820 438V390H840V370H850V390H875V350H893V420H925V319H943V310H952V425H976V371H998V395H1020V430Z"/><path d="M294 357V322H308V315H320V347H344V290H360V352H379V319H391V357Z"/></g><path d="M0 770Q320 600 590 810L680 900H0M1600 680L1300 635L1100 800L1000 900H1600" fill="#234453"/><path d="M940 475Q947 595 920 713" fill="none" stroke="#bdd6d0" opacity=".3" stroke-width="7"/></svg><svg class="mountains near" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice"><path d="M0 820L100 710L220 745L350 820L490 840L605 900H0M1600 760L1480 738L1360 809L1200 850L1160 900H1600" fill="#142d3b"/><path d="M440 900L680 825H920L1150 900Z" fill="#49555a"/><path d="M590 900L729 825M1000 900L861 825" stroke="#8e9185" stroke-opacity=".3"/></svg><div class="exit-pillar left"></div><div class="exit-pillar right"></div></div>
 <div class="ambient-dust"><i v-for="i in 19" :key="i" :style="{'--x':`${(i*31)%100}%`,'--y':`${(i*17)%90}%`,'--delay':`${-i*1.7}s`,'--duration':`${9+i%7}s`}"></i></div>
 <div v-if="!outside" class="light-array" :class="`count-${count}`"><div v-for="n in slots" :key="n" class="light-position" :class="{lit:lit.includes(n),flash:flash===n}"><div class="lamp-halo"></div><div class="lamp-stem"></div><div class="lamp"><div class="lamp-core"></div></div><span>{{String(n).padStart(2,'0')}}</span></div><p v-if="count===0" class="empty-circuit">∅<small>空回路 · 没有灯，也不需要命令</small></p></div>
 <Character v-if="showCharacter" :face="face" :class="{'in-shadow':!lit.length&&mode==='title'}"/>
 <div class="scene-vignette"></div>
</div>
</template>
