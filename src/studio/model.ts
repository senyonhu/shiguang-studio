export interface Asset { id: string; name: string; kind: 'background' | 'character'; src: string }
export interface Choice { label: string; next: string }
export interface StoryNode {
  id: string; title: string; type: 'dialogue' | 'choice' | 'level' | 'ending';
  speaker: string; text: string; background: string; character: string;
  next?: string; choices?: Choice[]; level?: string; locked?: boolean;
}
export interface Project { version: 1; id: string; title: string; chapter: string; entry: string; assets: Asset[]; nodes: StoryNode[] }
export interface Issue { path: string; message: string }
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const id = (v: unknown): v is string => typeof v === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(v);
export function inspectProject(value: unknown): Issue[] {
  const issues: Issue[] = [];
  const err = (path: string, message: string) => issues.push({ path, message });
  if (!record(value)) return [{ path: 'project', message: '工程必须是一个对象' }];
  if (value.version !== 1) err('version', '仅支持格式版本 1');
  if (!id(value.id)) err('id', '工程编号格式不正确');
  for (const field of ['title', 'chapter']) if (typeof value[field] !== 'string' || !(value[field] as string).trim() || (value[field] as string).length > 120) err(field, '请输入 1–120 字标题');
  if (!Array.isArray(value.assets) || !Array.isArray(value.nodes)) return [...issues, { path: 'project', message: '缺少素材或剧情列表' }];
  if (value.assets.length > 30 || value.nodes.length > 300 || !value.nodes.length) err('project', '体验版支持 1–300 个节点、最多 30 个素材');
  const assetIds = new Set<string>(), nodeIds = new Set<string>();
  const assetKinds = new Map<string, string>();
  value.assets.forEach((a: unknown, i: number) => {
    const p = `assets[${i}]`;
    if (!record(a)) { err(p, '素材格式不正确'); return; }
    if (!id(a.id) || assetIds.has(a.id)) err(p, '素材编号缺失或重复');
    else { assetIds.add(a.id); assetKinds.set(a.id, String(a.kind)); }
    if (a.kind !== 'background' && a.kind !== 'character') err(p, '素材类型不支持');
    if (typeof a.name !== 'string' || a.name.length > 120) err(p, '素材名称格式不正确');
    if (typeof a.src !== 'string' || !(/^(\/studio\/assets\/[a-zA-Z0-9_.-]+\.(png|webp|jpg|jpeg))$/.test(a.src) || /^data:image\/(png|jpeg|webp);base64,[a-zA-Z0-9+/=]+$/.test(a.src)) || a.src.length > 2800000) err(p, '只支持项目内图片或小于 2 MB 的导入图片');
  });
  value.nodes.forEach((n: unknown, i: number) => {
    const p = `nodes[${i}]`;
    if (!record(n)) { err(p, '节点格式不正确'); return; }
    if (!id(n.id) || nodeIds.has(n.id)) err(p, '节点编号缺失或重复'); else nodeIds.add(n.id);
    if (!['dialogue', 'choice', 'level', 'ending'].includes(String(n.type))) err(p, '节点类型不支持');
    for (const f of ['title', 'speaker', 'text', 'background', 'character']) if (typeof n[f] !== 'string') err(`${p}.${f}`, '必须是文字');
    if (typeof n.text === 'string' && n.text.length > 1200) err(`${p}.text`, '单段文字请控制在 1200 字内');
    if (typeof n.speaker === 'string' && n.speaker.length > 40) err(`${p}.speaker`, '角色名最多 40 字');
    if (typeof n.title === 'string' && n.title.length > 120) err(`${p}.title`, '节点标题最多 120 字');
    if (!assetIds.has(String(n.background)) || assetKinds.get(String(n.background)) !== 'background') err(`${p}.background`, '背景图片不存在');
    if (n.character !== '' && assetKinds.get(String(n.character)) !== 'character') err(`${p}.character`, '角色立绘不存在');
    if (n.locked !== undefined && typeof n.locked !== 'boolean') err(`${p}.locked`, '锁定状态必须是布尔值');
    if (n.type === 'level' && n.level !== 'constellation') err(`${p}.level`, '体验版仅包含星图关卡');
    if (n.type === 'choice' && (!Array.isArray(n.choices) || n.choices.length < 1 || n.choices.length > 6)) err(`${p}.choices`, '选择节点需要 1–6 个选项');
  });
  if (!nodeIds.has(String(value.entry))) err('entry', '故事入口不存在');
  value.nodes.forEach((n: unknown, i: number) => {
    if (!record(n)) return;
    if (n.type === 'dialogue' || n.type === 'level') { if (!nodeIds.has(String(n.next))) err(`nodes[${i}].next`, '下一段剧情不存在'); }
    if (Array.isArray(n.choices)) n.choices.forEach((c: unknown, j: number) => {
      if (!record(c) || typeof c.label !== 'string' || !c.label.trim() || c.label.length > 120 || !nodeIds.has(String(c.next))) err(`nodes[${i}].choices[${j}]`, '选项文字为空或跳转目标不存在');
    });
  });
  return issues;
}
export function parseProject(input: string): Project {
  if (input.length > 12000000) throw new Error('工程文件超过体验版 12 MB 限制');
  let value: unknown;
  try { value = JSON.parse(input); } catch { throw new Error('文件不是有效的 JSON 工程'); }
  const issues = inspectProject(value);
  if (issues.length) throw new Error(`${issues[0].path}：${issues[0].message}`);
  return value as Project;
}
export function advanceTo(project: Project, currentId: string, option?: number): string | null {
  const node = project.nodes.find(n => n.id === currentId);
  if (!node || node.type === 'ending' || node.type === 'level') return null;
  if (node.type === 'choice') return option !== undefined ? node.choices?.[option]?.next ?? null : null;
  return node.next ?? null;
}
