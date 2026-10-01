<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, computed } from 'vue';
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';
import 'monaco-editor/esm/vs/basic-languages/cpp/cpp.contribution';
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
const props=defineProps<{modelValue:string;task:number;fontSize:number;diagnostics:string;practice:boolean;locked:boolean}>();
const emit=defineEmits<{ 'update:modelValue':[string] }>();
const host=ref<HTMLElement>();let editor:monaco.editor.IStandaloneCodeEditor,model:monaco.editor.ITextModel,updating=false;
self.MonacoEnvironment={getWorker:()=>new EditorWorker()};
const readOnly=computed(()=>props.locked);
const scope=computed(()=>props.practice?'完整代码 · 自由实验':props.task===1?'可直接增删代码 · 试着把 0 改为 1':'可直接增删完整代码 · 蓝色行是建议关注区域');
const guidedMatch=computed(()=>props.task===1?props.modelValue.match(/printf\("(.*)\\n"\)/):props.modelValue.match(/for\s*\(int i = 1; (.*); i\+\+\)/));
const snippet=computed(()=>guidedMatch.value?.[1]??'');
function change(event:Event){if(props.locked)return;const text=(event.target as HTMLInputElement).value;const next=props.task===1?props.modelValue.replace(/(printf\(")(.*)(\\n"\))/,(_,before,_old,after)=>before+text+after):props.modelValue.replace(/(for\s*\(int i = 1; )(.*)(; i\+\+\))/,(_,before,_old,after)=>before+text+after);emit('update:modelValue',next);}
onMounted(()=>{
 monaco.editor.defineTheme('tower',{base:'vs-dark',inherit:true,rules:[{token:'keyword',foreground:'D6A8CF'},{token:'string',foreground:'D7BD83'},{token:'number',foreground:'8ACCC5'},{token:'comment',foreground:'637B86'}],colors:{'editor.background':'#0c1822','editor.foreground':'#C6D5DA','editorLineNumber.foreground':'#435967','editorLineNumber.activeForeground':'#9BACB1','editor.lineHighlightBackground':'#132632','editor.selectionBackground':'#2c4b5c','editorCursor.foreground':'#C7DCCF','editorWidget.background':'#14242e','editorIndentGuide.background1':'#1b303d'}});
 model=monaco.editor.createModel(props.modelValue,'cpp');editor=monaco.editor.create(host.value!,{model,theme:'tower',fontSize:props.fontSize,fontFamily:'"Cascadia Code", Consolas, monospace',minimap:{enabled:false},scrollBeyondLastLine:false,automaticLayout:true,lineNumbersMinChars:3,padding:{top:14,bottom:10},renderLineHighlight:'line',glyphMargin:false,folding:false,contextmenu:false,tabSize:4,wordWrap:'off',readOnly:readOnly.value,accessibilitySupport:'on',ariaLabel:'C 语言代码编辑器',overviewRulerLanes:0,scrollbar:{verticalScrollbarSize:6,horizontalScrollbarSize:6}});
 editor.onDidChangeModelContent(()=>{if(updating)return;const value=model.getValue();if(value.length>16000){updating=true;model.setValue(props.modelValue);updating=false;return;}emit('update:modelValue',value);});
 decorate();editor.revealLineInCenter(props.task===1?4:7);
});
let collection:monaco.editor.IEditorDecorationsCollection;
function decorate(){if(!editor)return;collection?.clear();const start=props.task===1?4:7,end=props.task===3?Math.max(7,model.getLineCount()-4):start;collection=editor.createDecorationsCollection([{range:new monaco.Range(start,1,end,100),options:{isWholeLine:true,className:'editable-code-line',linesDecorationsClassName:'editable-code-gutter'}}]);}
watch(()=>props.modelValue,value=>{if(model&&value!==model.getValue()){updating=true;model.setValue(value);updating=false;}decorate();});
watch(()=>[props.task,props.practice,props.fontSize,props.locked],()=>{editor?.updateOptions({readOnly:readOnly.value,fontSize:props.fontSize});decorate();});
watch(()=>props.diagnostics,text=>{if(!model)return;const markers=[...text.matchAll(/(?:main\.c|source\.c|\/box\/main\.c):(\d+):(\d+):\s*(?:fatal )?(error|warning):([^\n]+)/g)].map(m=>({startLineNumber:Number(m[1]),startColumn:Number(m[2]),endLineNumber:Number(m[1]),endColumn:Number(m[2])+1,message:m[4],severity:m[3]==='error'?monaco.MarkerSeverity.Error:monaco.MarkerSeverity.Warning}));monaco.editor.setModelMarkers(model,'compiler',markers);});
onBeforeUnmount(()=>{editor?.dispose();model?.dispose();});
</script>
<template><div class="code-editor"><div class="file-bar"><span><i class="c-file">C</i> tower_light.c</span><small>C11 <span class="tiny-dot"></span> UTF-8</small></div><div ref="host" class="monaco-host"></div><div class="edit-scope"><span class="scope-dot"></span><span>{{locked?'本关已提交 · 通关后可继续自由实验':scope}}</span><label v-if="task<3&&!practice&&guidedMatch" class="guided-input"><span>{{task===1?'输出内容':'循环条件'}}</span><input :value="snippet" @input="change" :disabled="locked" :aria-label="task===1?'printf 输出内容':'循环条件'" spellcheck="false" maxlength="100"/></label></div></div></template>
