<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { Sparkles, Play, ChevronRight, ChevronLeft, Plus, Check, X, Download, Upload, Image as ImageIcon, MessageSquare, GitBranch, Puzzle, Flag, BookOpen, Settings2, PanelLeftClose, ArrowUpRight, LockKeyhole, CircleHelp, RotateCcw, Expand, Layers, FileJson, ShieldCheck, CircleAlert, Star, FolderOpen, Pencil, Eye, ArrowLeft, Save, VolumeX } from 'lucide-vue-next';
import { advanceTo, inspectProject, parseProject, type Project, type StoryNode } from './model';

const PROJECT_KEY = 'shiguang-studio-project-v1', PLAY_KEY = 'shiguang-studio-play-v1';
const project = ref<Project | null>(null), selected = ref('lantern');
const tab = ref<'story' | 'assets' | 'review'>('story');
const inPlay = ref(false), levelOpen = ref(false), showHelp = ref(false), showConfig = ref(false), showChecks = ref(false);
const notice = ref(''), fatal = ref(''), saveState = ref('已保存到本机'), loading = ref(true);
const iframe = ref<HTMLIFrameElement>(), projectInput = ref<HTMLInputElement>(), imageInput = ref<HTMLInputElement>();
const levelSteps = ref([2, 5, 3]), hasProgress = ref(false), levelReady = ref(false);
const dismissed = ref<string[]>([]), audioMessage = ref(false);
let saveTimer: ReturnType<typeof setTimeout>, noticeTimer: ReturnType<typeof setTimeout>, readyTimer: ReturnType<typeof setTimeout>;
let levelToken = '', levelDone = false;
const node = computed(() => project.value?.nodes.find(n => n.id === selected.value) ?? project.value?.nodes[0]);
const index = computed(() => project.value?.nodes.findIndex(n => n.id === selected.value) ?? 0);
const bg = computed(() => project.value?.assets.find(a => a.id === node.value?.background)?.src);
const character = computed(() => project.value?.assets.find(a => a.id === node.value?.character)?.src);
const issues = computed(() => project.value ? inspectProject(project.value) : []);
const backgrounds = computed(() => project.value?.assets.filter(a => a.kind === 'background') ?? []);
const reloadPage = () => window.location.reload();
const characters = computed(() => project.value?.assets.filter(a => a.kind === 'character') ?? []);
const types = { dialogue: '对白', choice: '选择分支', level: 'HTML 关卡', ending: '章节结束' };
const icons = { dialogue: MessageSquare, choice: GitBranch, level: Puzzle, ending: Flag };
const proposals = [
  { id: 'p1', node: 'lantern', label: '让重逢多一点迟疑', reason: '保留原意，加入动作与停顿，让璃的等待更具体。', before: '你终于来了。\n我还以为，今晚又只剩下我和这盏灯了。', after: '……是你吗？\n她把灯举高了一点。\n「太好了。今晚，我终于不用一个人等天亮了。」' },
  { id: 'p2', node: 'relight', label: '用画面收束这一幕', reason: '减少直接抒情，让海面上的灯光回应前面的等待。', before: '亮了……你看，海面上也有光了。\n原来等待真的会有回声。谢谢你，愿意停下来。', after: '你看……海上，有一艘船亮起了灯。\n璃悄悄松开了攥紧的衣角。\n「它找到回家的路了。我们也是。」' }
];
const pending = computed(() => proposals.filter(p => !dismissed.value.includes(p.id) && project.value?.nodes.find(n => n.id === p.node)?.text !== p.after));
function notify(text: string) { notice.value = text; clearTimeout(noticeTimer); noticeTimer = setTimeout(() => notice.value = '', 4800); }
function persist() {
  if (!project.value) return;
  try { localStorage.setItem(PROJECT_KEY, JSON.stringify(project.value)); saveState.value = '已保存到本机'; }
  catch { saveState.value = '保存失败，请导出备份'; notify('本机存储空间不足。请导出工程，避免丢失修改。'); }
}
watch(project, () => { if (loading.value) return; saveState.value = '正在保存…'; clearTimeout(saveTimer); saveTimer = setTimeout(persist, 250); }, { deep: true });
function selectNode(id: string) { selected.value = id; tab.value = 'story'; levelOpen.value = false; }
function setField(field: 'speaker' | 'text' | 'background' | 'character' | 'title', value: string) {
  if (!node.value) return;
  if (node.value.locked) { notify('这段内容已锁定，请先解除锁定。'); return; }
  node.value[field] = value;
}
function proposalState(p: typeof proposals[number]) {
  const n = project.value?.nodes.find(n => n.id === p.node);
  if (!n) return 'missing';
  if (n.text === p.after) return 'accepted';
  if (dismissed.value.includes(p.id)) return 'dismissed';
  if (n.locked) return 'locked';
  return n.text === p.before ? 'pending' : 'conflict';
}
function accept(p: typeof proposals[number]) {
  if (proposalState(p) !== 'pending') { notify('原文已经修改或锁定，未覆盖你的内容。'); return; }
  project.value!.nodes.find(n => n.id === p.node)!.text = p.after;
  notify('已采用这一条修改。可在剧情预览中查看。');
}
function undo(p: typeof proposals[number]) {
  const n = project.value?.nodes.find(n => n.id === p.node);
  if (!n || n.locked || n.text !== p.after) { notify('内容已被继续修改或锁定，未执行撤回。'); return; }
  n.text = p.before;
  notify('已恢复修改前的台词。');
}
function progressSnapshot() {
  if (!project.value || !inPlay.value) return;
  try { localStorage.setItem(PLAY_KEY, JSON.stringify({ project: JSON.stringify(project.value), node: selected.value, steps: levelSteps.value })); hasProgress.value = true; }
  catch { notify('试玩进度未能保存，请先导出工程。'); }
}
function play(fromHere = false) {
  if (issues.value.length) { showChecks.value = true; return; }
  if (!fromHere) { selected.value = project.value!.entry; levelSteps.value = [2, 5, 3]; }
  inPlay.value = true; tab.value = 'story'; progressSnapshot();
}
function resume() {
  try {
    const v = JSON.parse(localStorage.getItem(PLAY_KEY) ?? 'null');
    if (!v || v.project !== JSON.stringify(project.value)) throw new Error('工程内容已变化，请从头试玩或从当前段落试玩。');
    if (!project.value?.nodes.some(n => n.id === v.node) || !validSteps(v.steps)) throw new Error('试玩存档无效，请重新试玩。');
    selected.value = v.node; levelSteps.value = v.steps; inPlay.value = true; tab.value = 'story'; notify('已恢复上次试玩进度。');
  } catch (e) { notify((e as Error).message); }
}
function exitPlay() { progressSnapshot(); inPlay.value = false; levelOpen.value = false; }
function next(option?: number) {
  if (!project.value || !node.value) return;
  const target = advanceTo(project.value, selected.value, option);
  if (target) { selected.value = target; progressSnapshot(); }
}
function previous() { if (project.value && index.value > 0) selected.value = project.value.nodes[index.value - 1].id; }
function validSteps(v: unknown): v is number[] { return Array.isArray(v) && v.length === 3 && v.every(n => Number.isInteger(n) && n >= 0 && n < 8); }
async function enterLevel() {
  if (node.value?.type !== 'level') return;
  levelToken = crypto.randomUUID(); levelDone = false; levelReady.value = false; levelOpen.value = true;
  await nextTick();
  clearTimeout(readyTimer); readyTimer = setTimeout(() => { if (!levelReady.value && levelOpen.value) notify('关卡加载超时，可以关闭后重试。'); }, 8000);
}
function initLevel() {
  iframe.value?.contentWindow?.postMessage({ type: 'studio:init', token: levelToken, steps: [...levelSteps.value] }, '*');
}
function handleMessage(event: MessageEvent) {
  if (!levelOpen.value || event.source !== iframe.value?.contentWindow) return;
  const data = event.data;
  if (!data || typeof data !== 'object' || data.token !== levelToken) return;
  if (data.type === 'level:ready') { levelReady.value = true; clearTimeout(readyTimer); }
  if (data.type === 'level:checkpoint' && validSteps(data.steps)) { levelSteps.value = data.steps; progressSnapshot(); }
  if (data.type === 'level:complete' && !levelDone && validSteps(data.steps) && data.steps.every((s: number) => s === 0) && node.value?.type === 'level') {
    levelDone = true; levelOpen.value = false; selected.value = node.value.next!; levelSteps.value = [2, 5, 3]; progressSnapshot(); notify('星图已修复 · 结果已返回剧情');
  }
}
function closeLevel() { levelOpen.value = false; progressSnapshot(); }
function exportProject() {
  if (!project.value) return;
  const blob = new Blob([JSON.stringify(project.value, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob), a = document.createElement('a'); a.href = url; a.download = `${project.value.id}.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  notify('已导出配置。内置素材仍来自本演示，自选图片已包含在配置内。');
}
async function importProject(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]; if (!file) return;
  try {
    if (file.size > 12000000) throw new Error('工程不能超过 12 MB');
    const candidate = parseProject(await file.text());
    if (!confirm('导入会替换工作台中的当前工程。请确认已导出需要保留的修改。')) return;
    project.value = candidate; selected.value = candidate.entry; dismissed.value = []; levelSteps.value = [2, 5, 3]; inPlay.value = false; levelOpen.value = false; persist(); notify('工程已导入，可以直接预览。');
  } catch (e) { notify((e as Error).message); } finally { input.value = ''; }
}
async function importImage(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]; if (!file || !project.value) return;
  try {
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 2000000) throw new Error('请选择小于 2 MB 的 PNG、JPG 或 WebP 图片');
    if (project.value.assets.length >= 30) throw new Error('素材数量已达体验版上限');
    const src = await new Promise<string>((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.onerror = reject; r.readAsDataURL(file); });
    await new Promise<void>((resolve, reject) => { const img = new window.Image(); img.onload = () => resolve(); img.onerror = () => reject(new Error('图片无法解码，请换一张图片')); img.src = src; });
    const asset = { id: `bg-${Date.now()}`, name: file.name.slice(0, 120), kind: 'background' as const, src };
    project.value.assets.push(asset); if (node.value && !node.value.locked) node.value.background = asset.id;
    notify('背景已导入。可在右侧为每段剧情选择背景。');
  } catch (e) { notify((e as Error).message); } finally { input.value = ''; }
}
async function reset() {
  if (!confirm('还原会清除本工作台的修改和试玩进度。需要保留的内容请先导出。')) return;
  try { const r = await fetch('/studio/project.json'); if (!r.ok) throw new Error(); project.value = parseProject(await r.text()); selected.value = 'lantern'; dismissed.value = []; localStorage.removeItem(PLAY_KEY); hasProgress.value = false; levelSteps.value = [2, 5, 3]; persist(); notify('已还原样板内容。'); } catch { notify('还原失败，当前工程未主动清除。'); }
}
function keyHandler(e: KeyboardEvent) {
  if (!inPlay.value || levelOpen.value || showHelp.value || showConfig.value || showChecks.value || ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes((e.target as HTMLElement)?.tagName)) return;
  if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); next(); }
  if (e.key === 'Escape') exitPlay();
}
onMounted(async () => {
  window.addEventListener('message', handleMessage); window.addEventListener('keydown', keyHandler);
  try {
    const saved = localStorage.getItem(PROJECT_KEY);
    if (saved) { try { project.value = parseProject(saved); } catch { notify('上次工程格式异常，已载入原始样板；旧内容未自动覆盖。'); } }
    if (!project.value) { const r = await fetch('/studio/project.json'); if (!r.ok) throw new Error('样板工程加载失败'); project.value = parseProject(await r.text()); }
    if (!project.value.nodes.some(n => n.id === selected.value)) selected.value = project.value.entry;
    hasProgress.value = !!localStorage.getItem(PLAY_KEY);
    const assets = project.value.assets.map(async asset => { const img = new window.Image(); img.src = asset.src; await img.decode(); });
    try { await Promise.race([Promise.all(assets), new Promise((_, reject) => setTimeout(() => reject(new Error('图片加载超时')), 10000))]); }
    catch { notify('部分图片未能载入，请在素材库检查资源。'); }
  } catch (e) { fatal.value = (e as Error).message; } finally { loading.value = false; }
});
onUnmounted(() => { window.removeEventListener('message', handleMessage); window.removeEventListener('keydown', keyHandler); clearTimeout(saveTimer); clearTimeout(noticeTimer); clearTimeout(readyTimer); });
</script>

<template>
  <div v-if="loading || fatal" class="boot"><Sparkles :size="32"/><h1>拾光 Studio</h1><p>{{fatal || '正在布置你的故事…'}}</p><button v-if="fatal" @click="reloadPage">重新加载</button></div>
  <div v-else-if="project && node" class="studio" :class="{ 'play-mode': inPlay }">
    <header class="topbar">
      <a class="brand" href="/studio.html"><span class="brand-mark"><Sparkles :size="23"/></span><b>拾光<span>STUDIO</span></b><small>体验版</small></a>
      <div class="project-crumb"><span></span>{{ project.title }}<ChevronRight :size="13"/><small>制作工作台</small></div>
      <div class="top-actions"><span class="saved-state"><i></i>{{saveState}}</span><button class="icon-button" title="体验指南" aria-label="体验指南" @click="showHelp = true"><CircleHelp :size="18"/></button><button class="button quiet" @click="exportProject"><Download :size="15"/>导出配置</button><button class="button primary" @click="play(false)"><Play :size="14" fill="currentColor"/>试玩故事</button></div>
    </header>

    <aside class="sidebar">
      <div class="workspace-name"><span class="workspace-symbol">光</span><div><b>旧塔的最后一盏灯</b><small>我的第一个故事工程</small></div></div>
      <nav class="nav-tabs"><button :class="{active: tab === 'story'}" @click="tab = 'story'"><BookOpen :size="17"/>剧情编排<span>{{ project.nodes.length }}</span></button><button :class="{active: tab === 'assets'}" @click="tab = 'assets'"><ImageIcon :size="17"/>素材库<span>{{project.assets.length}}</span></button><button :class="{active: tab === 'review'}" @click="tab = 'review'"><Sparkles :size="17"/>AI 变更审查<span class="count-accent">{{pending.length}}</span></button></nav>
      <div class="sidebar-label">故事章节 <span>01</span></div>
      <button class="chapter-item" @click="tab = 'story'"><ChevronRight :size="14" class="rotate"/><FolderOpen :size="16"/><span>序章 · 风停在旧塔</span></button>
      <div class="node-tree"><button v-for="(n, i) in project.nodes" :key="n.id" :class="{selected: selected === n.id && tab === 'story'}" @click="selectNode(n.id)"><span class="node-number">{{String(i + 1).padStart(2, '0')}}</span><component :is="icons[n.type]" :size="14"/><span>{{n.title}}</span><LockKeyhole v-if="n.locked" :size="12"/></button></div>
      <div class="sidebar-bottom"><div class="hint-card"><span><Sparkles :size="16"/>从一个小改动开始</span><p>改一句台词，再按下试玩。<br/>你的故事，会立刻回应。</p><button @click="showHelp = true">查看体验指南 <ArrowUpRight :size="14"/></button></div><button class="project-settings" @click="showConfig = true"><FileJson :size="15"/>查看工程配置<ChevronRight :size="14"/></button><button class="project-settings" @click="projectInput?.click()"><Upload :size="15"/>导入工程<ChevronRight :size="14"/></button></div>
    </aside>

    <main class="main-panel">
      <nav class="mobile-tabs" aria-label="工作台导航"><button :class="{active:tab === 'story'}" @click="tab = 'story'"><BookOpen :size="14"/>剧情</button><button :class="{active:tab === 'assets'}" @click="tab = 'assets'"><ImageIcon :size="14"/>素材</button><button :class="{active:tab === 'review'}" @click="tab = 'review'"><Sparkles :size="14"/>审查 {{pending.length}}</button></nav>
      <template v-if="tab === 'story'">
        <div class="page-heading"><div><div class="eyebrow">YOUR STORY, FRAME BY FRAME</div><h1>{{project.chapter}} <span>草稿</span></h1><p>让想象成为画面，让每一句话被看见。</p></div><button class="button outline" @click="showChecks = true"><ShieldCheck :size="15"/>工程检查<span class="check-count" :class="{bad: issues.length}">{{issues.length ? issues.length : '✓'}}</span></button></div>
        <div class="stage-card">
          <div class="stage-toolbar"><span><i></i>实时预览 <small>与右侧内容同步</small></span><div><span class="aspect-label">16 : 9</span><button title="从当前段落试玩" aria-label="从当前段落试玩" @click="play(true)"><Expand :size="15"/></button></div></div>
          <div class="stage" :class="{ending: node.type === 'ending'}">
            <img class="stage-bg" :src="bg" alt="月下观星室背景" @error="notify('背景图片加载失败，请检查素材路径。')"/>
            <div class="stage-vignette"></div><div class="stage-grain"></div>
            <div class="scene-location"><span>PROLOGUE</span><b>旧塔 · 观星室</b></div>
            <button v-if="inPlay" class="back-studio" @click="exitPlay"><ArrowLeft :size="15"/>返回工作台</button>
            <div class="motes"><i v-for="i in 9" :key="i" :style="{'--i': i}"></i></div>
            <img v-if="character && node.type !== 'ending'" class="character" :src="character" alt="璃的角色立绘" @error="notify('立绘加载失败，请检查素材。')"/>
            <div v-if="node.type === 'level'" class="level-invite"><span class="large-star">✧</span><p>一场属于星光的解谜</p><h2>失落的星图</h2><span>转动星环，让光重新找到方向。</span><button class="game-button" @click="enterLevel"><Puzzle :size="17"/>进入星图</button><small>独立 HTML 关卡 · 完成后继续剧情</small></div>
            <div v-else-if="node.type === 'ending'" class="ending-invite"><span>THE LAST LIGHT</span><h2>{{node.text.split('\n')[0]}}</h2><p>{{node.text.split('\n').slice(1).join(' ')}}</p><div><button class="game-button" @click="play(false)"><RotateCcw :size="16"/>再看一次</button><button class="game-button ghost" @click="exitPlay">回到工作台</button></div></div>
            <template v-else><div v-if="node.type === 'choice'" class="story-choices"><button v-for="(choice, i) in node.choices" :key="i" @click="next(i)"><span>0{{i + 1}}</span>{{choice.label}}<ChevronRight :size="15"/></button></div><div class="dialogue-box" :class="{compact: node.type === 'choice'}"><div class="speaker"><span></span>{{node.speaker}}<small v-if="node.speaker === '璃'">守塔人</small></div><p>{{node.text}}</p><div class="dialogue-footer"><span>✧</span><button v-if="node.type === 'dialogue'" @click="next()">{{inPlay ? '继续' : '预览下一段'}}<ChevronRight :size="14"/></button><span v-else>选择你的回应</span></div></div></template>
          </div>
          <div class="stage-footer"><span><Eye :size="14"/>{{types[node.type]}}<i>·</i><code>{{node.id}}</code></span><div><button title="上一节点" aria-label="上一节点" :disabled="index === 0" @click="previous"><ChevronLeft :size="15"/></button><span>{{String(index + 1).padStart(2, '0')}} <small>/ {{String(project.nodes.length).padStart(2, '0')}}</small></span><button title="下一节点" aria-label="下一节点" :disabled="index >= project.nodes.length - 1" @click="selectNode(project.nodes[index + 1].id)"><ChevronRight :size="15"/></button></div></div>
        </div>
        <div class="below-stage"><div class="section-heading"><h3><Layers :size="16"/>这一幕的内容</h3><span>点击卡片，在右侧修改</span></div><div class="event-cards"><button class="event-card" @click="tab = 'assets'"><div class="event-thumb"><img :src="bg" alt="当前背景缩略图"/></div><div><small>场景背景</small><b>{{project.assets.find(a => a.id === node?.background)?.name}}</b></div><ChevronRight :size="14"/></button><button class="event-card text-event" @click="showConfig = true"><span class="event-icon"><component :is="icons[node.type]" :size="20"/></span><div><small>{{types[node.type]}}</small><b>{{node.speaker}}<span v-if="node.type === 'choice'"> · {{node.choices?.length}} 个分支</span></b></div><span class="pill">配置驱动</span></button></div><div class="workflow-note"><Sparkles :size="14"/><span>AI 负责写，人负责把关。</span><button @click="tab = 'review'">看看 AI 建议改了什么 <ArrowUpRight :size="13"/></button></div></div>
      </template>

      <template v-if="tab === 'assets'"><div class="page-heading"><div><div class="eyebrow">A WORLD WORTH SEEING</div><h1>故事的素材库</h1><p>实际插画，独立资源。让故事拥有自己的样子。</p></div><button class="button primary" @click="imageInput?.click()"><Plus :size="15"/>导入背景</button></div><div class="asset-info"><ImageIcon :size="17"/>已收录 {{project.assets.length}} 份图片素材 · 本次演示使用 AI 生成的原创样板插画</div><div class="asset-grid"><article v-for="asset in project.assets" :key="asset.id" class="asset-card"><div :class="['asset-art', asset.kind]"><img :src="asset.src" :alt="asset.name"/></div><div class="asset-details"><span>{{asset.kind === 'background' ? '场景背景' : '透明立绘'}}</span><h3>{{asset.name}}</h3><button class="button outline" @click="setField(asset.kind === 'background' ? 'background' : 'character', asset.id); tab = 'story'">用于当前段落 <ArrowUpRight :size="14"/></button></div></article><button class="asset-upload" @click="imageInput?.click()"><Plus :size="28"/><b>放进你自己的世界</b><span>导入 PNG / JPG / WebP 背景</span><small>单张不超过 2 MB · 随工程配置导出</small></button></div></template>

      <template v-if="tab === 'review'"><div class="page-heading"><div><div class="eyebrow">HUMAN IN THE LOOP</div><h1>每一处修改，由你决定。</h1><p>先看变化，再决定采用。你锁定的段落不会被覆盖。</p></div><span class="review-badge"><Sparkles :size="15"/>{{pending.length}} 条待审查</span></div><div class="sample-banner"><CircleHelp :size="17"/><div><b>这是一组预置的 AI 修改示例</b><p>用于体验对比、采用、撤回和冲突保护；本演示没有接入实时 AI 服务。</p></div></div><article class="review-card" v-for="p in proposals" :key="p.id"><header><div><small>{{p.node}}</small><h3>{{p.label}}</h3></div><span class="pill">仅修改台词</span></header><p class="review-reason">{{p.reason}}</p><div class="diff-columns"><div class="before"><span>修改前</span><p>{{p.before}}</p></div><div class="after"><span><Sparkles :size="13"/>建议修改</span><p>{{p.after}}</p></div></div><footer><button class="text-button" @click="selectNode(p.node)">定位到剧情 <ArrowUpRight :size="14"/></button><div v-if="proposalState(p) === 'pending'"><button class="button quiet" @click="dismissed.push(p.id)">保留原文</button><button class="button primary" @click="accept(p)"><Check :size="14"/>采用修改</button></div><div v-else-if="proposalState(p) === 'accepted'"><span class="accepted"><Check :size="15"/>已采用</span><button class="button quiet" @click="undo(p)">撤回修改</button></div><span v-else-if="proposalState(p) === 'dismissed'" class="muted">已保留原文 <button class="text-button" @click="dismissed = dismissed.filter(id => id !== p.id)">重新审查</button></span><span v-else class="conflict"><LockKeyhole :size="14"/>{{proposalState(p) === 'locked' ? '这段内容已锁定' : proposalState(p) === 'missing' ? '当前工程中没有这个节点' : '原文已由你修改，需人工合并'}}</span></footer></article></template>
    </main>

    <aside class="inspector">
      <div class="inspector-title"><h2><Settings2 :size="17"/>段落属性</h2><span>实时生效</span></div>
      <div class="inspector-body"><div class="node-identity"><span class="inspector-node-icon"><component :is="icons[node.type]" :size="20"/></span><div><b>{{node.title}}</b><code>{{node.id}}</code></div></div>
      <div class="field-label">内容类型<span>{{types[node.type]}}</span></div>
      <label class="field"><span>说话的人</span><input aria-label="说话的人" :value="node.speaker" :disabled="node.locked" maxlength="40" @input="setField('speaker', ($event.target as HTMLInputElement).value)"/></label>
      <label class="field"><span>台词内容 <small>{{node.text.length}} / 1200</small></span><textarea aria-label="台词内容" :value="node.text" :disabled="node.locked" maxlength="1200" @input="setField('text', ($event.target as HTMLTextAreaElement).value)"></textarea></label><p class="field-tip"><Pencil :size="12"/>修改后，左侧画面会立即更新</p>
      <div class="inspector-divider"></div><h3 class="inspector-section"><ImageIcon :size="15"/>画面与角色</h3>
      <label class="field"><span>场景背景</span><select aria-label="场景背景" :disabled="node.locked" :value="node.background" @change="setField('background', ($event.target as HTMLSelectElement).value)"><option v-for="a in backgrounds" :key="a.id" :value="a.id">{{a.name}}</option></select></label><div class="bg-preview"><img :src="bg" alt="所选背景"/><button @click="imageInput?.click()"><Upload :size="13"/>换成自己的图片</button></div>
      <label class="field"><span>人物立绘</span><select aria-label="人物立绘" :disabled="node.locked" :value="node.character" @change="setField('character', ($event.target as HTMLSelectElement).value)"><option value="">不显示人物</option><option v-for="a in characters" :key="a.id" :value="a.id">{{a.name}}</option></select></label>
      <div v-if="node.type === 'choice'" class="branch-list"><h3 class="inspector-section"><GitBranch :size="15"/>分支去向</h3><button v-for="c in node.choices" :key="c.next" @click="selectNode(c.next)"><span>{{c.label}}</span><small>{{project.nodes.find(n => n.id === c.next)?.title}} <ArrowUpRight :size="12"/></small></button></div>
      <div class="inspector-divider"></div><label class="lock-control"><span><LockKeyhole :size="15"/><b>锁定此段内容</b></span><input type="checkbox" aria-label="锁定此段内容" v-model="node.locked"/></label><p class="field-tip lock-tip">保留你的决定，防止后续 AI 修改覆盖。</p>
      <button class="button outline inspector-preview" @click="play(true)"><Play :size="14"/>从这一段开始试玩</button><button v-if="hasProgress" class="text-button resume-button" @click="resume"><Save :size="13"/>继续上次试玩</button>
      </div><div class="inspector-foot"><ShieldCheck :size="14"/><span>内容独立于引擎 · 修改无需编程</span></div>
    </aside>

    <div v-if="inPlay" class="play-hint">{{node.type === 'dialogue' ? '空格 / Enter 继续' : node.type === 'choice' ? '选择一个回应，走进故事' : '你的每一步，都会被记住'}}<span>自动保存试玩进度</span><button @click="audioMessage = !audioMessage"><VolumeX :size="15"/>无声美术预览</button><small v-if="audioMessage">本版未接入音频，先体验画面与制作流程。</small></div>
    <div v-if="notice" class="toast" role="status"><Check :size="16"/>{{notice}}</div>
    <div v-if="levelOpen" class="modal-backdrop level-backdrop"><section class="level-modal" role="dialog" aria-modal="true" aria-label="失落的星图"><header><span><Puzzle :size="17"/>失落的星图 <small>独立 HTML 关卡</small></span><button aria-label="退出关卡" @click="closeLevel"><X :size="20"/></button></header><iframe ref="iframe" src="/studio/levels/constellation.html" sandbox="allow-scripts" title="转动星环解谜" @load="initLevel"></iframe><footer><span :class="{connected: levelReady}"><i></i>{{levelReady ? '已连接剧情引擎' : '正在加载关卡…'}}</span><span>进度会保留 · 完成后自动衔接剧情</span></footer></section></div>
    <div v-if="showHelp || showConfig || showChecks" class="modal-backdrop" @click.self="showHelp = showConfig = showChecks = false"><section class="info-modal" :class="{wide: showConfig}" role="dialog" aria-modal="true" :aria-label="showHelp ? '体验指南' : showConfig ? '工程配置' : '工程检查'"><button class="modal-close" aria-label="关闭弹窗" @click="showHelp = showConfig = showChecks = false"><X :size="20"/></button>
      <template v-if="showHelp"><span class="modal-emblem"><Sparkles :size="26"/></span><div class="eyebrow">MAKE IT YOURS</div><h2>先玩一下，再做决定。</h2><p class="modal-intro">这份可操作的样板，让你提前体验未来的制作方式。</p><ol class="guide-list"><li><span>01</span><div><b>改一句台词</b><p>在右侧修改文字，看它立即出现在画面里。</p></div></li><li><span>02</span><div><b>审查一次 AI 修改</b><p>打开“AI 变更审查”，逐条采用、保留或撤回示例。</p></div></li><li><span>03</span><div><b>走进故事，修复星图</b><p>点击“试玩故事”，体验分支、HTML 解谜和结尾。</p></div></li></ol><p class="scope-note">已实现：图片、文字、分支、关卡、配置导入导出与本机保存。实时 AI、音视频和完整发行功能尚未接入。</p><button class="button primary full-width" @click="showHelp = false; play(false)"><Play :size="15"/>现在试玩</button></template>
      <template v-if="showConfig"><div class="eyebrow">ONE SOURCE OF TRUTH</div><h2>你看到的故事，就在这份配置里。</h2><p class="modal-intro">界面修改与 AI 修改共用这一份数据。当前为只读预览，可导出编辑后重新导入。</p><pre class="config-code">{{JSON.stringify(project, null, 2)}}</pre><div class="modal-actions"><button class="button quiet" @click="reset"><RotateCcw :size="14"/>还原样板</button><button class="button primary" @click="exportProject"><Download :size="14"/>导出配置</button></div></template>
      <template v-if="showChecks"><span class="modal-emblem"><ShieldCheck :size="26"/></span><h2>{{issues.length ? '发现需要修正的内容' : '工程结构检查通过'}}</h2><p class="modal-intro">检查配置格式、素材引用、节点编号和分支目标。实际文件可用性及故事质量仍需试玩确认。</p><ul v-if="issues.length" class="issue-list"><li v-for="issue in issues" :key="issue.path"><b>{{issue.path}}</b>{{issue.message}}</li></ul><div v-else class="checks-list"><p><Check :size="16"/>{{project.nodes.length}} 个剧情节点 · 编号唯一</p><p><Check :size="16"/>角色与背景素材引用有效</p><p><Check :size="16"/>所有选择与跳转目标存在</p><p><Check :size="16"/>HTML 关卡类型受支持</p></div><button class="button primary full-width" @click="showChecks = false">知道了</button></template>
    </section></div>
    <input ref="projectInput" hidden type="file" accept="application/json,.json" @change="importProject"/><input ref="imageInput" hidden type="file" accept="image/png,image/jpeg,image/webp" @change="importImage"/>
  </div>
</template>

