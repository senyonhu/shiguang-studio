// 插件包结构检查（本机可验证的部分）：
//   1. 必需文件存在（清单、MCP 连接配置、Skill 与 references、适配器构建产物）
//   2. SKILL.md 有合法 frontmatter（name/description），name 与包名一致
//   3. Skill 不重复罗列工具参数（参数以 tools/list 为准），只引用工具名
//   4. 适配器构建产物不包含写文件 API（工具层不能绕过后台直接改写工程文件）
//   5. 包内没有凭据/工程运行数据（配对密钥、会话文件、.studio 目录）
// 用法：node scripts/studio-plugin-check.mjs [--json <out>]
// 说明：本脚本只做静态检查；“宿主能否加载插件”必须按目标宿主规范单独验证，不由本脚本证明。
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { argValue } from './studio-lib.mjs';

const ROOT = path.resolve(fileURLToPath(new URL('..', import.meta.url)));
const PLUGIN_DIR = path.join(ROOT, 'plugin', 'studio-author');
const checks = [];
const fail = (id, message) => checks.push({ id, status: 'failed', message });
const pass = (id, message, details) => checks.push({ id, status: 'passed', message, details });
const warn = (id, message, details) => checks.push({ id, status: 'warning', message, details });

function readText(rel) {
  return readFileSync(path.join(PLUGIN_DIR, rel), 'utf8');
}

function walk(dir, prefix = '') {
  const out = [];
  if (!existsSync(dir)) return out;
  for (const name of readdirSync(dir)) {
    const abs = path.join(dir, name);
    if (statSync(abs).isDirectory()) out.push(...walk(abs, `${prefix}${name}/`));
    else out.push(`${prefix}${name}`);
  }
  return out;
}

// 1. 必需文件
const required = ['.codex-plugin/plugin.json', '.mcp.json', 'skills/studio-author/SKILL.md', 'skills/studio-author/references/project-model.md', 'skills/studio-author/references/media-workflow.md', 'server/mcp-stdio.mjs'];
const missing = required.filter((rel) => !existsSync(path.join(PLUGIN_DIR, rel)));
if (missing.length) fail('PLUGIN-FILES', `缺少必需文件：${missing.join(', ')}`);
else pass('PLUGIN-FILES', '清单、MCP 配置、Skill、references 与适配器构建产物均存在');

// 2. 清单与 SKILL frontmatter
let pluginJson = null;
try {
  pluginJson = JSON.parse(readText('.codex-plugin/plugin.json'));
  const declaredServer = pluginJson.server;
  if (!pluginJson.name || !pluginJson.version || !pluginJson.description) fail('PLUGIN-MANIFEST', '清单缺少 name/version/description');
  else if (declaredServer && !existsSync(path.join(PLUGIN_DIR, declaredServer))) fail('PLUGIN-MANIFEST', `清单声明的适配器入口不存在：${declaredServer}`);
  else pass('PLUGIN-MANIFEST', `清单字段可解析：name=${pluginJson.name} version=${pluginJson.version}`, { manifestStatus: pluginJson.manifestStatus?.state ?? 'unknown' });
  if (pluginJson.manifestStatus?.state === 'unverified_host_schema') {
    warn('PLUGIN-MANIFEST-HOST', '清单字段按方案结构示意编写，尚未在目标宿主规范下验证（KI-022/KI-001）');
  }
} catch (error) {
  fail('PLUGIN-MANIFEST', `plugin.json 不可解析：${error.message}`);
}

const skillText = readText('skills/studio-author/SKILL.md');
const frontmatter = /^---\r?\n([\s\S]*?)\r?\n---/.exec(skillText);
if (!frontmatter) fail('SKILL-FRONTMATTER', 'SKILL.md 缺少 YAML frontmatter');
else {
  const name = /^name:\s*(.+)$/m.exec(frontmatter[1])?.[1]?.trim();
  const description = /^description:\s*(.+)$/m.exec(frontmatter[1])?.[1]?.trim();
  if (!name || !description) fail('SKILL-FRONTMATTER', 'frontmatter 必须包含 name 与 description');
  else if (name !== pluginJson?.name) fail('SKILL-FRONTMATTER', `Skill name(${name}) 与插件名(${pluginJson?.name}) 不一致`);
  else if (description.length < 40) fail('SKILL-FRONTMATTER', 'description 太短，宿主难以判断何时加载');
  else pass('SKILL-FRONTMATTER', `Skill ${name} 的 frontmatter 合法（本机格式与 resources/runtime/office-skills 一致）`);
}

// 3. 参数不重复罗列
const schemaLike = /"properties"\s*:/.test(skillText) || /inputSchema/.test(skillText);
if (schemaLike) fail('SKILL-NO-SCHEMA-DUPLICATION', 'SKILL.md 中出现了 Schema 片段；参数规范应只由 tools/list 提供');
else pass('SKILL-NO-SCHEMA-DUPLICATION', 'SKILL.md 不复制工具参数清单，只引用工具名与 tools/list');

const referencedTools = [...new Set([...skillText.matchAll(/`(studio_[a-z_]+)`/g)].map((match) => match[1]))].sort();
if (referencedTools.length < 10) fail('SKILL-TOOLS', `SKILL.md 只引用了 ${referencedTools.length} 个工具，制作流程覆盖不足`);
else pass('SKILL-TOOLS', `SKILL.md 引用了 ${referencedTools.length} 个工具名`, { tools: referencedTools });

const referenceText = [
  'skills/studio-author/references/project-model.md',
  'skills/studio-author/references/media-workflow.md',
  'skills/studio-author/SKILL.md',
  'README.md',
].map((rel) => { try { return readText(rel); } catch { return ''; } }).join('\n');
// 与实现不符的表述（两边都要拦）：既不能谎称未接入的能力已接通，也不能把已实现的能力说成未实现。
const staleClaims = ['media-jobs 已实现', '生成服务已接通', '真实服务已接入', '音频未实现播放', '视频未实现播放', '展示器尚未实现音频', '展示器尚未实现视频', '未实现播放'];
const hit = staleClaims.filter((claim) => referenceText.includes(claim));
if (hit.length) fail('SKILL-TRUTHFULNESS', `references 中出现与实现不符的表述：${hit.join('; ')}`);
else pass('SKILL-TRUTHFULNESS', 'Skill/README/references 与实现一致（生成服务标注为未接入；音视频播放标注为已实现）');

// 关卡协议契约：AI 生成的关卡必须实现 init.snapshot 恢复与 checkpoint，否则玩家读档会丢进度。
const levelContract = [];
if (!referenceText.includes('snapshot')) levelContract.push('未说明 init.snapshot 恢复契约');
if (!referenceText.includes('checkpoint')) levelContract.push('未说明 checkpoint 进度点');
if (!referenceText.includes('complete')) levelContract.push('未说明 complete 完成消息');
if (levelContract.length) fail('PLUGIN-SKILL-LEVEL-CONTRACT', `关卡协议说明不完整：${levelContract.join('；')}`);
else pass('PLUGIN-SKILL-LEVEL-CONTRACT', 'references 说明了关卡协议与快照恢复契约（init.snapshot / checkpoint / complete）');

// 4. 适配器不写工程文件 + 构建产物与工具注册表一致
const bundlePath = path.join(PLUGIN_DIR, 'server', 'mcp-stdio.mjs');
if (existsSync(bundlePath)) {
  const bundle = readFileSync(bundlePath, 'utf8');
  const writeApis = ['writeFileSync', 'appendFileSync', 'createWriteStream', 'writeFile(', 'rmSync(', 'unlinkSync', 'renameSync', 'copyFileSync', 'mkdirSync'];
  const found = writeApis.filter((api) => bundle.includes(api));
  if (found.length) fail('PLUGIN-NO-FILE-WRITES', `适配器构建产物包含写文件 API：${found.join(', ')}`);
  else pass('PLUGIN-NO-FILE-WRITES', '适配器构建产物不含写文件 API（工具只能通过后台 API 修改工程）');
  if (/child_process/.test(bundle)) pass('PLUGIN-AUTOSTART', '适配器包含按配置启动后台的能力（仅启动后台进程，不写工程文件）');

  // 构建产物必须覆盖注册表里的全部工具：避免改了 registry 却忘记 npm run build:plugin
  const registry = readFileSync(path.join(ROOT, 'server', 'src', 'tools', 'registry.ts'), 'utf8');
  const registryTools = [...new Set([...registry.matchAll(/name: '(studio_[a-z_]+)'/g)].map((match) => match[1]))].sort();
  const missing = registryTools.filter((name) => !bundle.includes(name));
  if (missing.length) fail('PLUGIN-BUNDLE-FRESH', `构建产物缺少 ${missing.length} 个已注册工具（请重新运行 npm run build:plugin）：${missing.join(', ')}`);
  else pass('PLUGIN-BUNDLE-FRESH', `构建产物包含注册表中的全部 ${registryTools.length} 个工具`, { toolCount: registryTools.length });
} else {
  fail('PLUGIN-BUNDLE-FRESH', '缺少适配器构建产物，无法检查工具覆盖情况');
}

// 5. 包内不含凭据或工程运行数据
const forbidden = [/pairing\.key$/i, /sessions\.json$/i, /\.studio-service/i, /^\.studio\//i, /credentials/i, /token/i, /\.env$/i];
const files = walk(PLUGIN_DIR);
const offenders = files.filter((rel) => forbidden.some((pattern) => pattern.test(rel)));
if (offenders.length) fail('PLUGIN-NO-SECRETS', `包内含疑似凭据/运行数据的文件：${offenders.join(', ')}`);
else pass('PLUGIN-NO-SECRETS', `包内 ${files.length} 个文件不含凭据、会话文件或 .studio 运行数据`);

const report = {
  kind: 'studio-plugin-check',
  checkedAt: new Date().toISOString(),
  pluginDir: path.relative(ROOT, PLUGIN_DIR).replace(/\\/g, '/'),
  fileCount: files.length,
  checks,
  summary: {
    passed: checks.filter((check) => check.status === 'passed').length,
    failed: checks.filter((check) => check.status === 'failed').length,
    warnings: checks.filter((check) => check.status === 'warning').length,
  },
  notVerifiedHere: [
    '目标 AI 宿主能否加载本插件与 Skill（需要真实宿主，见 review/studio/known-issues.md KI-001）',
    '宿主清单字段与 MCP 连接配置的字段名是否符合宿主规范（KI-022）',
  ],
};

const out = argValue(process.argv, '--json');
if (out) {
  const abs = path.resolve(ROOT, out);
  writeFileSync(abs, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  console.log(`报告已写入 ${path.relative(ROOT, abs)}`);
}
for (const check of checks) console.log(`[${check.status}] ${check.id}: ${check.message}`);
console.log(`passed=${report.summary.passed} failed=${report.summary.failed} warnings=${report.summary.warnings}`);
if (report.summary.failed > 0) process.exitCode = 1;
