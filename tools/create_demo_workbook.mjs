import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const output = path.join(root, 'outputs', 'formal-demo');
const materialsRoot = path.join(root, 'evidence', 'm8-demo', 'materials');
const evidence = path.join(root, 'evidence', 'm8-demo', 'workbook');
await fs.mkdir(output, { recursive: true });
await fs.mkdir(materialsRoot, { recursive: true });
await fs.mkdir(evidence, { recursive: true });

const headers = ['幕', '帧号', '背景图片', '人物图片', '附加图片', '说话人', '对白或旁白', '配音文件', '背景音乐', '音效', '画面衔接', '下一帧或选项', '制作意图'];
const station = 'images/station.png';
const roof = 'images/rooftop.png';
const neutral = 'images/linxi-neutral.png';
const smile = 'images/linxi-smile.png';
const ticket = 'images/ticket.png';
const bgm = 'audio/bgm.wav';
const none = '无';
const rows = [
  ['第一幕', 1, station, neutral, none, '旁白', '傍晚的雨还没停。旧车站的灯映在水洼里，林汐站在熟悉的站台边。', none, bgm, none, '淡入', '推进后进入第2帧', '显示成年林汐的中性立绘。整幅画面由AI排版。'],
  ['第一幕', 2, station, smile, none, '林汐', '你还记得这个站台吗？我们第一次见面，就在这盏灯下面。', 'audio/voice-f02.wav', bgm, none, '柔和淡化', '推进后进入第3帧', '用微笑立绘替换第1帧中性立绘，只显示一张林汐。'],
  ['第一幕', 3, station, neutral, ticket, '旁白', '她摊开掌心。那张旧车票的边角已经泛白，日期却还清清楚楚。', none, bgm, 'audio/chime.wav', '柔和淡化', '推进后进入第4帧', '人物恢复中性。展示车票图片，不另行补写车票上的字。'],
  ['第一幕', 4, station, neutral, ticket, '林汐', '车票已经不能用了。不过，今天我们可以不用赶车。', 'audio/voice-f04.wav', bgm, none, '柔和淡化', '推进后进入第5帧', '保留本帧明确指定的车票与人物。配音逐字对应对白。'],
  ['第一幕', 5, station, neutral, none, '林汐', '雨快停了。你想先做什么？', none, bgm, none, '柔和淡化', '选项1：聊聊那次初遇，进入第6帧\n选项2：收好这张车票，进入第7帧\n选项3：一起去看晚霞，进入第8帧', '显示三个普通文字选项。此帧不显示车票图片。'],
  ['第一幕', 6, station, neutral, none, '林汐', '那天你问我车会不会晚点。我说，会。但我们还是一起等到了最后。', none, bgm, none, '柔和淡化', '推进后进入第9帧', '选项1的短分支，随后回到共同路线。'],
  ['第一幕', 7, station, neutral, ticket, '林汐', '那就先替我保管吧。等下次再回来，我们再一起看看它。', none, bgm, none, '柔和淡化', '推进后进入第9帧', '选项2的短分支，车票再次出现，随后回到共同路线。'],
  ['第一幕', 8, station, neutral, none, '林汐', '好。我知道一处屋顶，从那里能看见河岸和远处的灯。', none, bgm, none, '柔和淡化', '推进后进入第9帧', '选项3的短分支，随后回到共同路线。'],
  ['第二幕', 9, roof, none, none, none, none, none, none, none, '淡入', '点击推进后进入第10帧', '雨后屋顶的纯背景过场。清除人物、车票、对白与所有声音，完整呈现空画面状态。'],
  ['第二幕', 10, roof, smile, none, '林汐', '雨停了。再等一会儿，晚霞也许会从那边出来。', 'audio/voice-f10.wav', bgm, 'audio/chime.wav', '柔和淡化', '推进后进入第11帧', '屋顶上重新出现微笑的林汐。由AI安排人物与对白框。'],
  ['第二幕', 11, roof, smile, none, '旁白', '风把雨后的凉意带上来。她没有催我离开，我也终于不再看时间。', none, bgm, none, '柔和淡化', '推进后进入第12帧', '保持本帧指定的微笑人物与屋顶背景，不显示车票。'],
  ['第二幕', 12, roof, smile, none, '林汐', '下次雨停之前，也陪我再等一会儿吧。', none, bgm, none, '柔和淡化', '这句对白推进后结束作品', '三条选择都到达这里。以普通对白结束，不加入特殊互动。'],
];
const assets = [
  ['车站背景', '背景图片', station, '第1–8帧', '傍晚旧车站，雨中的灯光。人物不画入背景。'],
  ['屋顶背景', '背景图片', roof, '第9–12帧', '雨后的屋顶、远处河岸和晚霞。人物不画入背景。'],
  ['林汐中性', '透明人物立绘', neutral, '第1、3–8帧', '林汐，26岁成年女性，中性表情。'],
  ['林汐微笑', '透明人物立绘', smile, '第2、10–12帧', '同一位林汐、同样衣着与构图，微笑表情。'],
  ['旧车票', '附加图片', ticket, '第3、4、7帧', '有岁月痕迹的旧车票图片。摆放由AI决定。'],
  ['第2帧配音', '中文语音', 'audio/voice-f02.wav', '第2帧', rows[1][6]],
  ['第4帧配音', '中文语音', 'audio/voice-f04.wav', '第4帧', rows[3][6]],
  ['第10帧配音', '中文语音', 'audio/voice-f10.wav', '第10帧', rows[9][6]],
  ['轻音乐', '背景音乐', bgm, '第1–8、10–12帧', '柔和器乐循环。第9帧不播放。'],
  ['提示音', '短音效', 'audio/chime.wav', '第3、10帧', '短而轻的提示音，不覆盖对白或语音。'],
];

await fs.writeFile(path.join(materialsRoot, 'voice-lines.json'), JSON.stringify({
  title: '雨停之前', language: 'zh-CN', speaker: '林汐（26岁成年女性）',
  lines: [2, 4, 10].map(frame => ({ frame_number: frame, relative_path: rows[frame - 1][7], text: rows[frame - 1][6] }))
}, null, 2), 'utf8');
await fs.writeFile(path.join(materialsRoot, 'story-spec.json'), JSON.stringify({
  title: '雨停之前', material_root: materialsRoot, source_type: '中文分镜材料',
  columns: headers, rows: rows.map(row => Object.fromEntries(headers.map((name, index) => [name, row[index]]))),
  choices: [{ option_number: 1, description: '聊聊那次初遇', next_frame: 6 }, { option_number: 2, description: '收好这张车票', next_frame: 7 }, { option_number: 3, description: '一起去看晚霞', next_frame: 8 }],
  merge_frame: 9, ending_frame: 12,
}, null, 2), 'utf8');

const workbook = Workbook.create();
const intro = workbook.worksheets.add('制作说明');
const frames = workbook.worksheets.add('分镜材料');
const catalog = workbook.worksheets.add('素材清单');
const palette = { ink: '#26313E', accent: '#33475B', pale: '#F1F5F8', line: '#BAC8D1', purple: '#EDE9F3' };
const baseFont = { name: 'Arial', size: 11, color: palette.ink };
function body(sheet, range) {
  sheet.showGridLines = false;
  const target = sheet.getRange(range);
  target.format.font = baseFont;
  target.format.verticalAlignment = 'top';
  target.format.wrapText = true;
}
function title(sheet, cell, value) {
  sheet.getRange(cell).values = [[value]];
  sheet.getRange(cell).format.font = { ...baseFont, size: 16, bold: true };
  sheet.getRange(cell).format.wrapText = false;
  sheet.getRange(cell).format.rowHeight = 30;
}
function header(sheet, range) {
  const target = sheet.getRange(range);
  target.format.fill = palette.accent;
  target.format.font = { ...baseFont, bold: true, color: '#FFFFFF' };
  target.format.horizontalAlignment = 'center';
  target.format.verticalAlignment = 'center';
  target.format.rowHeight = 30;
  target.format.borders = { insideVertical: { style: 'thin', color: '#FFFFFF' } };
}

body(intro, 'A1:B19');
title(intro, 'A2', '雨停之前');
intro.getRange('A3').values = [['Demo制作材料']];
intro.getRange('A3').format.font = { ...baseFont, italic: true };
intro.getRange('A4:B4').values = [['项目', '制作要求']];
header(intro, 'A4:B4');
const instructions = [
  ['作品与场景', '雨停之前。第一幕是傍晚旧车站，第二幕是雨后屋顶。'],
  ['角色', '林汐，26岁成年女性。全篇只使用这一个可见人物角色。'],
  ['制作材料根目录', materialsRoot],
  ['文件路径', '分镜材料和素材清单中的路径均相对以上根目录。'],
  ['一帧的含义', '每帧是一轮推进前的完整状态。所有素材列独立指定。\n“无”表示清除该项，不继承上一帧，也不在游戏中显示这个字。'],
  ['画面排版', '由AI决定背景裁切、立绘与车票位置、尺寸、层级以及普通对白框。不提供坐标。'],
  ['文本呈现', '说话人写“旁白”时用普通旁白对白框。“林汐”时显示角色名。第9帧没有任何对白框，点击一次推进。'],
  ['分支', '第5帧的选项1、2、3分别到第6、7、8帧。三个短分支都在第9帧汇合，不改变其他长期状态。'],
  ['开始与结束', '从第1帧开始。第12帧的对白推进后结束作品。'],
  ['幕间衔接', '第9帧更换为屋顶背景，同时清空人物、车票、对白、配音、音乐与音效。第10帧按自己的完整素材清单重新显示。'],
  ['语音', '只给第2、4、10帧配音，文字逐字对应分镜对白。其他帧明确没有配音。'],
  ['音乐与音效', '轻音乐用于第1–8和第10–12帧。第9帧停止音乐。第3、10帧播放一次提示音。'],
  ['三张表的用途', '分镜材料记录每次推进的素材与流程。素材清单独立记录文件用途和配音文字，便于准备素材；同一文件只登记一次。'],
  ['文字来源', '本次Demo的原创中文剧情，由Codex按约定的幕、帧和小分支范围撰写。'],
];
intro.getRange(`A5:B${4 + instructions.length}`).values = instructions;
intro.getRange('A1:A19').format.columnWidth = 25;
intro.getRange('B1:B19').format.columnWidth = 99;
intro.getRange('A5:A18').format.font = { ...baseFont, bold: true };
intro.getRange('A5:B18').format.rowHeight = 28;
intro.getRange('A9:B9').format.rowHeight = 40;
intro.getRange('A10:B11').format.rowHeight = 34;
intro.getRange('A14:B14').format.rowHeight = 34;
intro.getRange('A2:B2').format.rowHeight = 30;
intro.getRange('A3:B3').format.rowHeight = 25;
intro.getRange('A4:B18').format.borders = { insideHorizontal: { style: 'thin', color: palette.line } };

body(frames, 'A1:M18');
title(frames, 'A2', '雨停之前 分镜材料');
frames.getRange('A3').values = [['2幕12帧。第5帧三选项，第6、7、8帧各自回到第9帧。']];
frames.getRange('A3').format.wrapText = false;
frames.getRange('A4').values = [[`所有路径相对素材根目录：${materialsRoot}`]];
frames.getRange('A4').format.font = { ...baseFont, italic: true };
frames.getRange('A4').format.wrapText = false;
frames.getRange('A5').values = [['“无”表示本帧清除该项。所有画面位置、尺寸与对白框由AI决定。']];
frames.getRange('A5').format.wrapText = false;
frames.getRange('A6:M6').values = [headers];
header(frames, 'A6:M6');
frames.getRange('A7:M18').values = rows;
const widths = [10, 7, 25, 29, 23, 10, 60, 27, 21, 21, 14, 44, 50];
widths.forEach((width, index) => frames.getRange(`${String.fromCharCode(65 + index)}1:${String.fromCharCode(65 + index)}18`).format.columnWidth = width);
frames.getRange('A7:M18').format.rowHeight = 46;
frames.getRange('A11:M11').format.rowHeight = 62;
frames.getRange('A15:M15').format.rowHeight = 58;
frames.getRange('B7:B18').setNumberFormat('0');
frames.getRange('B7:B18').format.horizontalAlignment = 'right';
for (let row = 7; row <= 18; row++) {
  if (row % 2 === 0) frames.getRange(`A${row}:M${row}`).format.fill = palette.pale;
}
frames.getRange('A15:M15').format.fill = palette.purple;
frames.getRange('A7:M18').format.borders = { insideHorizontal: { style: 'thin', color: palette.line } };
frames.freezePanes.freezeRows(6);
frames.freezePanes.freezeColumns(2);
frames.tabColor = palette.accent;

body(catalog, 'A1:E16');
title(catalog, 'A2', '雨停之前 素材清单');
catalog.getRange('A3').values = [['同一文件只列一次。每帧实际归属以分镜材料为准。']];
catalog.getRange('A3').format.wrapText = false;
catalog.getRange('A4').values = [[`素材根目录：${materialsRoot}`]];
catalog.getRange('A4').format.font = { ...baseFont, italic: true };
catalog.getRange('A4').format.wrapText = false;
catalog.getRange('A6:E6').values = [['素材名称', '类型', '相对文件路径', '所属帧', '素材内容或配音原文']];
header(catalog, 'A6:E6');
catalog.getRange('A7:E16').values = assets;
const assetWidths = [21, 20, 32, 25, 66];
assetWidths.forEach((width, index) => catalog.getRange(`${String.fromCharCode(65 + index)}1:${String.fromCharCode(65 + index)}16`).format.columnWidth = width);
catalog.getRange('A7:E16').format.rowHeight = 36;
for (let row = 8; row <= 16; row += 2) catalog.getRange(`A${row}:E${row}`).format.fill = palette.pale;
catalog.getRange('A7:E16').format.borders = { insideHorizontal: { style: 'thin', color: palette.line } };
catalog.freezePanes.freezeRows(6);

workbook.recalculate();
const inspections = [];
for (const [name, range] of [['制作说明', 'A4:B18'], ['分镜材料', 'A6:M18'], ['素材清单', 'A6:E16']]) {
  const inspected = await workbook.inspect({ kind: 'table', range: `${name}!${range}`, include: 'values,formulas',
                                            tableMaxRows: 16, tableMaxCols: 13, tableMaxCellChars: 180, maxChars: 22000 });
  inspections.push({ sheet: name, ndjson: inspected.ndjson });
}
const errors = await workbook.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',
                                        options: { useRegex: true, maxResults: 100 }, summary: 'final formula error scan', maxChars: 1500 });
const views = [['制作说明', 'A1:B18', 'instructions.png'], ['分镜材料', 'A1:M18', 'storyboard-overview.png'],
               ['分镜材料', 'A6:F18', 'storyboard-assets.png'], ['分镜材料', 'F6:M18', 'storyboard-text-and-flow.png'],
               ['素材清单', 'A1:E16', 'asset-catalog.png']];
for (const [sheetName, range, filename] of views) {
  const render = await workbook.render({ sheetName, range, scale: 1.4, format: 'png' });
  await fs.writeFile(path.join(evidence, filename), new Uint8Array(await render.arrayBuffer()));
}
const xlsxPath = path.join(output, 'demo-materials.xlsx');
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(xlsxPath);
const hash = crypto.createHash('sha256').update(await fs.readFile(xlsxPath)).digest('hex');
await fs.writeFile(path.join(evidence, 'inspection.json'), JSON.stringify({ inspections, formula_errors: errors.ndjson }, null, 2), 'utf8');
await fs.writeFile(path.join(evidence, 'report.json'), JSON.stringify({
  scope: 'formal_demo_original_xlsx_input', xlsx_path: xlsxPath, sha256: hash, frame_row_count: rows.length,
  act_count: 2, branch_frame: 5, option_count: 3, option_destinations: [6, 7, 8], merge_frame: 9, ending_frame: 12,
  asset_row_count: assets.length, voice_frame_numbers: [2, 4, 10], material_root: materialsRoot,
  sheet_names: ['制作说明', '分镜材料', '素材清单'], authoring_library: '@oai/artifact-tool',
  internal_engine_model_written: false, engine_invoked: false, coordinates_given: false,
  rendered_views: views.map(([sheet, range, filename]) => ({ sheet, range, path: path.join(evidence, filename) })),
  visual_review: 'pending', exported_at: new Date().toISOString(),
}, null, 2), 'utf8');
console.log(JSON.stringify({ status: 'exported_for_visual_review', xlsx_path: xlsxPath, sha256: hash,
                            report: path.join(evidence, 'report.json') }));
