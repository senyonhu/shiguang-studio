import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const original = path.join(root, 'outputs/formal-demo/demo-materials.xlsx');
const updated = path.join(root, 'outputs/formal-demo/demo-materials-updated.xlsx');
const evidence = path.join(root, 'evidence/m8-demo/workbook-update');
await fs.mkdir(evidence, { recursive: true });
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(original));
const inspectOnly = process.argv.includes('--inspect-only');
const before = await workbook.inspect({ kind: 'workbook,sheet,table', maxChars: 6500,
  tableMaxRows: 6, tableMaxCols: 13, tableMaxCellChars: 100 });
const originalHash = crypto.createHash('sha256').update(await fs.readFile(original)).digest('hex');
if (inspectOnly) {
  await fs.writeFile(path.join(evidence, 'before-inspection.ndjson'), before.ndjson);
  const view = await workbook.render({ sheetName: '分镜材料', range: 'F6:M18', scale: 1.4, format: 'png' });
  await fs.writeFile(path.join(evidence, 'before-storyboard.png'), new Uint8Array(await view.arrayBuffer()));
  console.log(JSON.stringify({ status: 'read_only_import_inspected', original_sha256: originalHash }));
} else {
  const frames = workbook.worksheets.getItem('分镜材料');
  const changes = [
    { cell: 'G9', frame_number: 3, before: frames.getRange('G9').values[0][0], after: '我把旧车票递给林汐，雨点落在我们的指尖。' },
    { cell: 'G18', frame_number: 12, before: frames.getRange('G18').values[0][0], after: '灯光在远处亮起。明天，我们再一起去新的地方。' },
  ];
  for (const change of changes) frames.getRange(change.cell).values = [[change.after]];
  workbook.recalculate();
  const finalInspect = await workbook.inspect({ kind: 'table', range: '分镜材料!A6:M18', include: 'values,formulas',
    tableMaxRows: 13, tableMaxCols: 13, tableMaxCellChars: 180, maxChars: 20000 });
  const errors = await workbook.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!',
    options: { useRegex: true, maxResults: 100 }, maxChars: 1500 });
  const views = [['制作说明', 'A1:B18', 'instructions.png'], ['分镜材料', 'A1:M18', 'storyboard-overview.png'],
    ['分镜材料', 'F6:M18', 'storyboard-text-and-flow.png'], ['素材清单', 'A1:E16', 'asset-catalog.png']];
  for (const [sheetName, range, filename] of views) {
    const render = await workbook.render({ sheetName, range, scale: 1.4, format: 'png' });
    await fs.writeFile(path.join(evidence, filename), new Uint8Array(await render.arrayBuffer()));
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(updated);
  const sourceUnchanged = crypto.createHash('sha256').update(await fs.readFile(original)).digest('hex') === originalHash;
  if (!sourceUnchanged) throw new Error('The frozen original was unexpectedly changed');
  const sha256 = crypto.createHash('sha256').update(await fs.readFile(updated)).digest('hex');
  await fs.writeFile(path.join(evidence, 'inspection.ndjson'), finalInspect.ndjson);
  await fs.writeFile(path.join(evidence, 'report.json'), JSON.stringify({ scope: 'formal_demo_two_cell_source_update',
    authoring_library: '@oai/artifact-tool', original_path: original, original_sha256: originalHash,
    updated_path: updated, updated_sha256: sha256, original_unchanged: sourceUnchanged, changes,
    sheet_names: ['制作说明', '分镜材料', '素材清单'], row_count: 12, voice_files_changed: false,
    visual_review: 'pending', formula_error_scan: errors.ndjson,
    rendered_views: views.map(([sheet, range, filename]) => ({ sheet, range, path: path.join(evidence, filename) })) }, null, 2));
  console.log(JSON.stringify({ status: 'exported_for_visual_review', updated_path: updated, sha256 }));
}
