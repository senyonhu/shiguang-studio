import type { TaskId } from './types';
export const first = '#include <stdio.h>\n\nint main(void) {\n    printf("0\\n");\n    return 0;\n}\n';
export const loop = '#include <stdio.h>\n\nint main(void) {\n    int n;\n    scanf("%d", &n);\n\n    for (int i = 1; i < n; i++) {\n        printf("%d\\n", i);\n    }\n\n    return 0;\n}\n';
export const ids:TaskId[] = ['tower_light_01','tower_light_02','tower_light_03'];
export const tasks = {
 tower_light_01:{chapter:'01',title:'第一束光',subtitle:'让黑暗，停在这里。',objective:'输出编号 1，唤醒你面前的第一盏灯。',hint:['灯上的编号是多少？','终端需要收到数字 1。','将 printf 中的 0 改成 1。'],initial:first,explain:'引号包住要输出的文字，\\n 表示换行。把编号交给终端，它会为你点灯。'},
 tower_light_02:{chapter:'02',title:'为什么最后一盏不亮',subtitle:'有时候，答案就在边界上。',objective:'输入 n = 3，依次输出 1、2、3，每行一个编号。先运行一次，观察缺了哪盏灯。',hint:['把 i = 3、n = 3 代入条件。','3 < 3 成立吗？条件不成立时，循环就结束了。','试试 i <= n，让最后一个编号也执行一次。'],initial:loop,explain:'n 是灯数；i 从 1 开始；条件成立才执行本轮；每轮结束，i 增加 1。'},
 tower_light_03:{chapter:'03',title:'点亮整座塔',subtitle:'让同一道术式，回应不同的世界。',objective:'让同一份代码通过 0、1、3、6 四组输入，打开通往外界的门。',hint:['灯数变了，代码仍然要有效。不要把 3 写死。','n = 0 时，应该没有任何输出；n = 6 时，需要输出到 6。','从 i = 1 开始，用 i <= n 判断，每轮输出 i 后增加 1。'],initial:loop,explain:'四组输入使用同一份代码快照。编辑代码会使验证结果失效。它们是代表用例，不是对任意整数的证明。'}
};
export const statusText:Record<string,string>={queued:'排队中',running:'编译并运行中',passed:'验证通过',wrong_answer:'程序结束 · 输出尚不符合要求',compile_error:'编译失败',runtime_error:'运行时错误',timeout:'运行超时 · 已终止',output_limit:'输出超限 · 已截断',service_unavailable:'执行服务暂不可用'};
export function lightEvents(output:string, n:number) {
 const text=output.replace(/\r\n/g,'\n');
 const lines=text.endsWith('\n')?text.slice(0,-1).split('\n'):text.split('\n');
 if(text==='') return [];
 const seen=new Set<number>();
 return lines.slice(0,80).map(line=>{const id=/^[1-9]\d*$/.test(line)?Number(line):0; const valid=id>=1&&id<=n; const duplicate=valid&&seen.has(id); if(valid)seen.add(id); return {id,valid,duplicate,raw:line};});
}
