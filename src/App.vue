<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch, defineAsyncComponent } from 'vue';
import { ArrowRight, Play, Volume2, VolumeX, Settings2, Download, Upload, X, RotateCcw, Lightbulb, ChevronRight, Check, LockKeyhole, Sparkles, BookOpen, CircleHelp, Terminal, LoaderCircle, TriangleAlert, Home, SkipForward, Maximize, Save as SaveIcon } from 'lucide-vue-next';
import TowerScene from './components/TowerScene.vue';
const CodeEditor=defineAsyncComponent(()=>import('./components/CodeEditor.vue'));
import { useGame } from './useGame';
import { statusText, ids, lightEvents } from './tasks';
const g=useGame();
const {s,screen,saved,notice,busy,result,dirty,lit,flash,playing,input,health,settingsOpen,hintOpen,quiz,quizFeedback,practice,task,taskNumber,lightCount,ready}=g;
const name=ref(''),nameOpen=ref(false),resetOpen=ref(false),outputTab=ref<'output'|'diagnostic'>('output'),fileInput=ref<HTMLInputElement>();
const debugAvailable=import.meta.env.DEV&&new URLSearchParams(location.search).has('debug');
const outside=computed(()=>screen.value==='cinematic'||screen.value==='ending'||['未知残响','谁在说话','怎么了','……没什么','第一章'].some(t=>s.line.includes(t)));
const speaker=computed(()=>s.line.split('：')[0]);
const dialogue=computed(()=>s.line.includes('：')?s.line.slice(s.line.indexOf('：')+1):s.line);
const events=computed(()=>lightEvents(result.value?.stdout??'',lightCount.value));
const errors=computed(()=>events.value.filter(e=>!e.valid||e.duplicate));
const missing=computed(()=>Array.from({length:lightCount.value},(_,i)=>i+1).filter(n=>!events.value.some(e=>e.valid&&e.id===n)));
const minutes=computed(()=>`${Math.floor(s.active_seconds/60)} 分 ${s.active_seconds%60} 秒`);
const diagnostics=computed(()=>result.value?.compile_output||result.value?.stderr||'没有编译或运行诊断。');
const selectedCase=ref(0);
watch(result,r=>{if(r&&r.status!=='queued'&&r.status!=='running'){outputTab.value=r.status==='compile_error'?'diagnostic':'output';selectedCase.value=Math.max(0,r.cases.length-1);}});
let noticeTimer:ReturnType<typeof setTimeout>;
watch(notice,()=>{clearTimeout(noticeTimer);if(notice.value)noticeTimer=setTimeout(()=>notice.value='',6500);});
function keydown(e:KeyboardEvent){if((e.target as HTMLElement)?.closest('input,textarea,.monaco-editor,button,select'))return;if(e.key===' '||e.key==='Enter'){if(screen.value==='story'&&!settingsOpen.value){e.preventDefault();void g.advance();}}if(e.key==='Escape'){settingsOpen.value=false;hintOpen.value=false;}}
function fullScreen(){if(document.fullscreenElement)void document.exitFullscreen();else void document.documentElement.requestFullscreen().catch(()=>notice.value='当前窗口不支持全屏。');}
async function importFile(event:Event){const f=(event.target as HTMLInputElement).files?.[0];if(f)await g.importSave(f);(event.target as HTMLInputElement).value='';}
onMounted(()=>{void g.initialize();document.addEventListener('keydown',keydown);});
onBeforeUnmount(()=>{g.dispose();clearTimeout(noticeTimer);document.removeEventListener('keydown',keydown);});
</script>

<template>
 <main class="game" :class="[screen,{'reduce-motion':s.settings.reducedMotion}]">
  <TowerScene :mode="screen" :lit="screen==='title'?[1]:lit" :count="screen==='title'?6:lightCount" :face="s.face" :flash="flash" :outside="outside" :show-character="screen!=='title'&&screen!=='cinematic'&&screen!=='ending'&&(s.completed_tasks.length>0||lit.length>0)" :reduced-motion="s.settings.reducedMotion"/>
  <header class="topbar">
   <button class="brand" @click="screen==='title'?null:g.goTitle()" aria-label="返回标题"><span class="brand-sigil">✧</span><span>术式纪元<small>AGE OF INCANTATIONS</small></span></button>
   <div class="chapter-label" v-if="screen!=='title'"><span>序章</span><i></i>旧塔的最后一盏灯</div>
   <nav><span class="save-state" v-if="screen!=='title'&&!practice"><span class="tiny-dot"></span>自动存档</span><button class="icon-button" :aria-label="s.settings.muted?'开启声音':'静音'" @click="g.toggleMute"><VolumeX v-if="s.settings.muted"/><Volume2 v-else/></button><button class="icon-button fullscreen" aria-label="全屏" @click="fullScreen"><Maximize/></button><button class="icon-button" aria-label="设置与存档" @click="settingsOpen=true"><Settings2/></button></nav>
  </header>

  <section v-if="screen==='title'" class="title-content">
   <p class="eyebrow"><span></span> 一段关于光、代码与你的故事</p>
   <h1>旧塔的<br/>最后一盏<span>灯</span><i>。</i></h1>
   <p class="english-title">THE LAST LIGHT OF THE OLD TOWER</p>
   <p class="title-description">世界沉默在黑暗里。<br/>而你写下的第一个数字，将成为光。</p>
   <div class="title-actions"><button class="primary start-button" @click="nameOpen=true"><span>开始旅程</span><ArrowRight/></button><button v-if="saved" class="text-button" @click="g.resume">继续旅程 <span>第 {{ids.indexOf(saved.current_task_id)+1}} 道术式</span><ChevronRight/></button><button v-else class="subtle-button" @click="settingsOpen=true"><Upload :size="15"/> 导入已有存档</button></div>
   <div class="title-meta"><span>视觉小说 × C 语言</span><i></i><span>约 15–20 分钟</span><i></i><span>序章体验版</span></div>
  </section>
  <div class="title-quote" v-if="screen==='title'"><span>“</span><p>在这里，一个数字<br/>就能决定我们看见什么。</p><small>—— 璃 · 守塔学徒</small></div>
  <footer class="title-footer" v-if="screen==='title'"><span>一盏灯，一个起点。</span><span>DEMO 0.1 <i>◇</i> 原创交互叙事</span></footer>

  <section v-if="screen==='story'" class="story-ui">
   <div class="location-tag"><span class="tiny-dot"></span>{{outside?'旧塔 · 通往晨光的出口':'旧塔 · 下层内室'}}</div>
   <div class="story-choices" v-if="s.choices.length"><button v-for="choice in s.choices" :key="choice.index" @click="g.advance(choice.index)"><span>{{String(choice.index+1).padStart(2,'0')}}</span>{{choice.text}}<ChevronRight/></button></div>
   <div class="dialogue-box" :class="{'echo-dialogue':speaker==='未知残响'}"><div class="speaker"><span>{{speaker}}</span><small>{{speaker==='璃'?'守塔学徒':speaker==='旁白'?'THE OLD TOWER':speaker==='未知残响'?'UNKNOWN ECHO':'旅人'}}</small></div><p>{{dialogue}}</p><button v-if="!s.choices.length" @click="g.advance()" class="next-dialogue">继续 <ChevronRight :size="16"/></button><span class="dialogue-key">{{s.choices.length?'做出你的选择':'SPACE / 点击继续'}}</span></div>
  </section>

  <section v-if="screen==='task'" class="task-layout">
   <aside class="scene-info">
    <div class="location-tag"><span class="tiny-dot"></span>旧塔 · {{taskNumber===1?'下层内室':taskNumber===2?'回廊回路':'出口灯阵'}}</div>
    <div class="mission-label"><span class="eyebrow">{{practice?'自由实验':'修复进度'}}</span><div class="chapter-dots"><span v-for="(id,i) in ids" :key="id" :class="{active:s.current_task_id===id,done:s.completed_tasks.includes(id)}"><Check v-if="s.completed_tasks.includes(id)" :size="13"/><template v-else>{{i+1}}</template><i v-if="i<2"></i></span></div></div>
    <div class="circuit-caption"><span>回路 {{task.chapter}}</span><p>{{lit.length.toString().padStart(2,'0')}} <small>/ {{lightCount.toString().padStart(2,'0')}} 盏已点亮</small></p><small>{{playing?'正在回放 stdout 输出':'灯光由程序的实际输出驱动'}}</small></div>
    <div class="companion-card"><span class="companion-name">璃<small>守塔学徒</small><span class="expression">{{s.face==='thinking'?'思考':s.face==='relieved'?'释然':'担忧'}}</span></span><p>{{ready?'做到了。准备好，我们继续往前走。':result?.status==='wrong_answer'&&taskNumber===2?'不是完全失效。前两盏都亮了，问题出在它什么时候停下来。':taskNumber===1?'别担心。先试着告诉终端，你想点亮哪一盏灯。':taskNumber===2?'看看终端实际收到了什么。第三盏灯，还在等它的编号。':'没有灯的回路也值得检查。空白，有时也是正确的答案。'}}</p><span class="companion-rule"></span></div>
    <p class="replay-note">点灯为输出回放，并非逐行调试轨迹。</p>
   </aside>
   <section class="terminal-panel" aria-label="术式终端">
    <div class="terminal-heading"><span><Terminal :size="15"/> 术式终端 <small>ARCANE TERMINAL</small></span><button class="service-status" :class="health" @click="g.checkHealth" :aria-label="'检查执行服务：'+health"><span class="tiny-dot"></span>{{health==='online'?'执行器已连接':health==='checking'?'检查连接中':'执行器未连接'}}</button></div>
    <div class="task-heading"><span class="task-index">{{task.chapter}}</span><div><p class="eyebrow">{{task.subtitle}}</p><h2>{{task.title}}</h2></div><span class="chapter-badge">{{practice?'实验':'任务 '+taskNumber+' / 3'}}</span></div>
    <p class="objective">{{task.objective}}</p>
    <div class="input-row"><span v-if="taskNumber===1"><span class="stdin-label">标准输入</span>无需输入</span><label v-else><span class="stdin-label">标准输入</span>n = <input type="number" min="0" max="6" step="1" :value="Number.isNaN(input)?'':input" @input="g.changeInput(($event.target as HTMLInputElement).value)" :disabled="busy||playing" aria-label="试运行灯数" aria-describedby="stdin-help"/></label><span class="format-rule" id="stdin-help">{{taskNumber===1?'每行一个编号':taskNumber===2?'填 0–6，运行时交给 scanf；提交验证用 3':'填 0–6，运行时交给 scanf；提交验证用 0、1、3、6'}}</span></div>
    <CodeEditor :model-value="s.code_drafts[s.current_task_id]" @update:model-value="g.edit" :task="taskNumber" :font-size="s.settings.fontSize" :diagnostics="result?.compile_output??''" :practice="practice" :locked="ready"/>
    <div class="editor-tools"><button @click="g.hint"><Lightbulb :size="15"/> 查看提示 <span v-if="s.hint_levels[s.current_task_id]">{{s.hint_levels[s.current_task_id]}} / 3</span></button><button @click="resetOpen=true" :disabled="busy||ready"><RotateCcw :size="13"/> 恢复初始代码</button></div>
    <section class="output-panel" aria-label="运行结果" aria-live="polite">
     <div class="output-tabs"><button :class="{selected:outputTab==='output'}" @click="outputTab='output'">{{taskNumber===3&&result?.mode==='submit'?'回路验证':'终端输出'}}</button><button :class="{selected:outputTab==='diagnostic'}" @click="outputTab='diagnostic'">编译与诊断 <span v-if="result?.compile_output">●</span></button><small v-if="result&&!busy">{{result.duration_ms}} ms</small><span class="output-status" v-if="busy"><LoaderCircle class="spin" :size="13"/>{{statusText[result?.status??'queued']}}</span></div>
     <div class="output-body" v-if="outputTab==='output'">
      <div v-if="!result" class="output-placeholder"><Terminal :size="20"/><p>{{dirty?'代码已修改 · 待重新验证':'终端正在等待你的第一道指令'}}</p><small>运行后，在这里查看程序的实际输出。</small></div>
      <template v-else>
       <div class="result-banner" :class="result.status"><Check v-if="result.passed" :size="16"/><TriangleAlert v-else-if="!busy" :size="16"/><span>{{statusText[result.status]}}{{result.passed&&result.mode==='run'?' · 当前输入':''}}</span><small v-if="result.passed&&result.mode==='run'">提交验证后推进剧情</small></div>
       <div v-if="taskNumber===3&&result.mode==='submit'" class="case-list"><div class="case-label"><span>输入</span><span>期望编号</span><span>实际编号</span><span>结果</span></div><button v-for="n in [0,1,3,6]" :key="n" @click="()=>{const c=result?.cases.find(c=>c.input===n);if(c){input=n;g.replay(c.stdout,n);}}" :disabled="busy||playing"><span>n = {{n}}</span><span>{{n===0?'∅ 空输出':Array.from({length:n},(_,i)=>i+1).join(' ')}}</span><span>{{result.cases.find(c=>c.input===n)?.stdout.replace(/\n/g,' ')||(result.cases.some(c=>c.input===n)?'∅ 空输出':'—')}}</span><span :class="{'pass-text':result.cases.find(c=>c.input===n)?.passed}">{{result.cases.find(c=>c.input===n)?(result.cases.find(c=>c.input===n)?.passed?'✓ 通过':'! 未通过'):'○ 待验证'}}</span></button></div>
       <template v-else><div class="stdout"><span class="stdout-label">stdout</span><pre>{{result.stdout||(busy?'等待执行结果…':'（空输出）')}}</pre></div><p class="output-explanation" v-if="result.status==='wrong_answer'">目标编号 [{{Array.from({length:lightCount},(_,i)=>i+1).join(', ')}}]；实际编号 [{{events.map(e=>e.raw).join(', ')}}]。{{missing.length?'缺少 '+missing.join('、')+' 号灯。':''}}{{errors.length?'包含无效或重复编号。':''}}</p><p v-if="taskNumber===1&&result.stdout==='0\n'" class="output-explanation">收到了编号 0，当前灯的编号是 1。</p></template>
       <p class="output-explanation" v-if="['timeout','runtime_error','output_limit'].includes(result.status)">{{result.stdout?'部分结果，运行失败。 ':''}}{{result.status==='timeout'?'术式没有按时结束，请检查循环能否停止。':result.status==='output_limit'?'输出达到 16 KiB 上限，请检查是否反复输出。':'程序异常结束，可在“编译与诊断”查看原始信息。'}}</p>
       <p class="output-explanation" v-if="result.status==='service_unavailable'">{{result.stderr||'请启动本地 Judge0 服务，然后重试。草稿已保留。'}}</p>
      </template>
     </div><div class="output-body" v-else><p v-if="result?.status==='compile_error'" class="output-explanation">C 编译器未能读懂这段代码。检查引号、分号与括号；下方为原始编译信息。</p><pre class="raw-diagnostics">{{diagnostics}}</pre></div>
    </section>
    <div class="terminal-actions"><button class="run-button" @click="g.run('run')" :disabled="busy||playing"><LoaderCircle v-if="busy" class="spin" :size="16"/><Play v-else :size="15"/>运行术式</button><button v-if="ready" class="primary" @click="g.success" :disabled="busy||playing">{{taskNumber===3?'打开塔门':'继续前行'}}<ArrowRight :size="17"/></button><button v-else class="primary" @click="g.run('submit')" :disabled="busy||playing"><Check :size="16"/>{{taskNumber===3?'验证全部回路':'提交验证'}}</button></div>
    <p class="terminal-footnote"><LockKeyhole :size="11"/>实际编译执行 C11 <span>·</span> {{taskNumber===3?'四组输入 · 同一代码快照':'通过以完整输出为准'}} <button v-if="practice" @click="g.endExperiment">返回结算 →</button></p>
   </section>
  </section>

  <div v-if="screen==='cinematic'" class="cinematic-ui"><div class="cinema-title"><p>THE WORLD BEYOND</p><h2>旧塔的最后一盏灯</h2><span>光的尽头，旅程才刚刚开始。</span></div><button class="skip-button" @click="g.finishCinematic">跳过演出 <SkipForward :size="15"/></button></div>

  <section v-if="screen==='ending'" class="ending-card"><span class="ending-sigil">✧</span><p class="eyebrow">序章 · 旅程记录</p><h1>你为旧塔，带来了光。</h1><p class="ending-description">这一次，把重要的话留在今天。<br/>第一章 · 初识术式，待续。</p><div class="stats"><div><strong>03 <small>/ 03</small></strong><span>已修复术式</span></div><div><strong>{{minutes}}</strong><span>前台体验用时</span></div><div><strong>{{s.submissions}}</strong><span>运行与提交次数</span></div></div><div class="reward"><Sparkles :size="20"/><div><b>初光之证</b><small>{{s.reward_grants.length?'已记录 · 100 金币与旅人徽记':'调试体验 · 不发放奖励'}}</small></div><Check :size="18"/></div><div class="learning-record"><p>提示使用 <span>{{ids.map((id,i)=>`任务${i+1}：${s.hint_levels[id]}/3`).join('　')}}</span></p><p>理解题首次回答 <span>{{s.first_answer===null?'未作答':s.first_answer===1?'正确':'需要进一步讨论'}}</span></p><small>隐藏页面时暂停计时；前台停留计入用时。体验记录不等于知识掌握度。</small></div><div class="ending-actions"><button class="primary" @click="g.experiment"><Terminal :size="16"/>继续实验</button><button class="secondary" @click="g.exportSave"><Download :size="16"/>导出旅程</button><button class="text-button" @click="g.goTitle">返回标题 <ArrowRight :size="15"/></button></div></section>

  <div v-if="hintOpen&&screen==='task'" class="hint-popover"><button class="close" aria-label="关闭提示" @click="hintOpen=false"><X :size="18"/></button><p class="eyebrow"><Lightbulb :size="15"/> 璃的小提示</p><p v-for="(hint,i) in task.hint.slice(0,s.hint_levels[s.current_task_id])" :key="i"><span>{{i+1}}.</span>{{hint}}</p><button v-if="s.hint_levels[s.current_task_id]<3" class="text-button" @click="g.hint">再给一点提示 <ChevronRight :size="14"/></button><details><summary><BookOpen :size="14"/> 术式笔记</summary><p>{{task.explain}}</p><p>printf 只输出普通文字。本游戏把每行编号解释为点灯命令。换行可以是 LF 或 CRLF；允许一个末尾换行，不能多空行或额外文字。</p></details><small>提示随时可用，不扣分。</small></div>

  <div v-if="nameOpen||resetOpen||settingsOpen||quiz" class="modal-backdrop" @click.self="()=>{if(!quiz){nameOpen=false;resetOpen=false;settingsOpen=false;}}">
   <section v-if="nameOpen" class="modal name-modal" role="dialog" aria-modal="true" aria-label="启程"><button class="close" aria-label="关闭" @click="nameOpen=false"><X/></button><span class="modal-sigil">✧</span><p class="eyebrow">在故事开始之前</p><h2>该怎么称呼你？</h2><p>璃会记住你的名字。</p><input v-model="name" placeholder="旅人" maxlength="24" aria-label="旅人昵称" @keydown.enter="()=>{nameOpen=false;g.start(name);}"/><small v-if="saved">开始新旅程将覆盖本机进度，可先在设置中导出存档。</small><button class="primary" @click="()=>{nameOpen=false;g.start(name);}">走进旧塔 <ArrowRight :size="17"/></button></section>
   <section v-if="resetOpen" class="modal" role="dialog" aria-modal="true" aria-label="恢复初始代码"><h2>重新检查这道术式？</h2><p>当前关卡的代码草稿将被初始代码替换。</p><div class="modal-actions"><button class="secondary" @click="resetOpen=false">保留草稿</button><button class="primary" @click="()=>{g.resetCode();resetOpen=false;}">恢复初始代码</button></div></section>
   <section v-if="settingsOpen" class="modal settings-modal" role="dialog" aria-modal="true" aria-label="设置与存档"><button class="close" aria-label="关闭设置" @click="settingsOpen=false"><X/></button><p class="eyebrow">旅途随记</p><h2>设置与存档</h2><label class="setting-row">环境音乐与音效<button class="secondary" @click="g.toggleMute">{{s.settings.muted?'已关闭':'已开启'}}</button></label><label class="setting-row">代码字体<select v-model.number="s.settings.fontSize" @change="screen!=='title'&&g.persist()"><option v-for="n in [13,15,17,19]" :value="n">{{n}} px</option></select></label><label class="setting-row">减少动态效果<input type="checkbox" v-model="s.settings.reducedMotion" @change="screen!=='title'&&g.persist()"/></label><div class="save-info"><SaveIcon :size="18"/><div><b>{{saved?'本机存档已就绪':'尚未开始旅程'}}</b><small>{{saved?new Date(saved.saved_at).toLocaleString():'进度将在启程后自动保存'}}</small></div></div><div class="modal-actions"><button class="secondary" @click="g.exportSave" :disabled="!saved"><Download :size="16"/>导出 JSON</button><button class="secondary" @click="fileInput?.click()"><Upload :size="16"/>导入存档</button></div><p class="modal-note">本地存档可用于换设备体验，不作为正式考试成绩。导入会替换当前进度。</p><button v-if="screen!=='title'" class="text-button" @click="()=>{settingsOpen=false;g.goTitle();}"><Home :size="15"/>保存并返回标题</button><button v-if="debugAvailable" class="text-button" @click="()=>{settingsOpen=false;g.debugJump();}">开发调试：跳转灯阵（不记录完成）</button></section>
   <section v-if="quiz" class="modal quiz-modal" role="dialog" aria-modal="true" aria-label="理解问题"><p class="eyebrow"><CircleHelp :size="15"/> 出发前，想一想</p><h2>一盏灯也没亮，<br/>为什么还通过了验证？</h2><p>当输入 n = 0 时……</p><button class="quiz-option" v-for="(text,i) in ['终端忽略了这一组输入。','第一次检查条件就不成立，不需要输出。','只要编译成功，任何输出都能通过。']" :key="i" @click="g.answer(i)"><span>{{['A','B','C'][i]}}</span>{{text}}</button><p class="quiz-feedback">{{quizFeedback}}</p><button class="primary" @click="g.continueSuccess">{{quizFeedback.startsWith('正确')?'我明白了，出发':'先出发，稍后再想'}}<ArrowRight :size="16"/></button></section>
  </div>
  <input ref="fileInput" type="file" accept="application/json,.json" class="hidden" @change="importFile"/>
  <div v-if="notice" class="toast" role="status">{{notice}}<button aria-label="关闭通知" @click="notice=''"><X :size="14"/></button></div>
  <div class="debug-banner" v-if="s.debug&&screen!=='title'">调试体验 · 不计入真实完成记录 <button @click="g.replay('1\n2\n3\n4\n5\n6\n',6)">回放六灯</button> <button @click="g.debugJump('task')">重置灯阵</button> <button @click="g.debugJump('cinematic')">出口</button> <button @click="g.debugJump('ending')">结算</button></div>
 </main>
</template>
