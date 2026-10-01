// v1 节点图映射：先分配每个节点的首事件，再连接入口、对白 next 和选择分支。
// 纯映射阶段会拒绝缺失目标/重复节点；关卡未安装时沿 next 跨过并明确记录警告。
export function planV1Events(source, sceneId, migrationId, assetIds = new Map()) {
  const nodes = source.nodes;
  const byId = new Map();
  const warnings = [];
  for (const node of nodes) {
    if (!node || typeof node.id !== 'string' || !node.id || byId.has(node.id)) throw new Error('节点编号缺失或重复');
    byId.set(node.id, node);
  }
  const supported = (node) => ['dialogue', 'choice', 'ending'].includes(node.type);
  const resolve = (id) => {
    const visited = new Set();
    for (;;) {
      const node = byId.get(id);
      if (!node) throw new Error(`剧情目标 ${String(id)} 不存在`);
      if (supported(node)) return node.id;
      if (visited.has(node.id)) throw new Error(`未迁移节点形成循环：${node.id}`);
      visited.add(node.id);
      id = node.next;
    }
  };
  const blocks = new Map();
  const ids = (index, role) => `ev_mig${migrationId}${index.toString(36)}${role}`;
  for (const [index, node] of nodes.entries()) {
    if (!supported(node)) {
      warnings.push(node.type === 'level'
        ? `关卡节点 ${node.id}（${node.level}）未自动迁移：请用 studio_register_level + studio_install_level 安装；跳转沿 next 继续`
        : `未知节点类型 ${String(node.type)}（节点 ${node.id}）未迁移；跳转沿 next 继续`);
      continue;
    }
    const block = [];
    const insert = (role, type, params) => block.push({ action: 'event.insert', payload: { sceneId, eventId: ids(index, role), type, params } });
    if (node.background && assetIds.has(node.background)) {
      insert('bg', 'background', { assetId: assetIds.get(node.background) });
    } else if (node.background) warnings.push(`节点 ${node.id} 的背景 ${node.background} 未迁移（素材未登记）`);
    // v1 的立绘是节点状态，进入任何分支前移除其它立绘，避免上一分支残留。
    for (const [assetIndex, asset] of (source.assets ?? []).entries()) {
      if (asset.kind === 'character' && asset.id !== node.character && assetIds.has(asset.id)) {
        insert(`exit${assetIndex.toString(36)}`, 'character', { assetId: assetIds.get(asset.id), action: 'exit', position: 'center' });
      }
    }
    if (node.character && assetIds.has(node.character)) {
      insert('ch', 'character', { assetId: assetIds.get(node.character), action: 'enter', position: 'center' });
    } else if (node.character) warnings.push(`节点 ${node.id} 的人物 ${node.character} 未迁移（素材未登记）`);
    if (node.type === 'dialogue') {
      insert('main', 'dialogue', { speaker: node.speaker || '', text: node.text || node.title || '(空台词)' });
      if (node.next) insert('next', 'jump', { targetNodeId: resolve(node.next) });
      else {
        warnings.push(`对白节点 ${node.id} 没有 next：迁移为终止路径`);
        insert('stop', 'ending', { endingId: `end_${migrationId}${index.toString(36)}`, title: node.title || '结束' });
      }
    } else if (node.type === 'choice') {
      if (!Array.isArray(node.choices) || !node.choices.length) throw new Error(`选择节点 ${node.id} 没有选项`);
      insert('main', 'choice', {
        prompt: node.title || node.text || '请选择',
        options: node.choices.map((choice) => ({ label: choice.label, targetNodeId: resolve(choice.next) })),
      });
    } else insert('main', 'ending', { endingId: `end_${migrationId}${index.toString(36)}`, title: node.title || '结束' });
    blocks.set(node.id, block);
  }
  if (!blocks.size) throw new Error('没有可迁移的对白、选择或结束节点');
  const entry = resolve(source.entry ?? nodes[0].id);
  const eventId = (nodeId) => blocks.get(nodeId)[0].payload.eventId;
  const operations = [
    { action: 'event.insert', payload: { sceneId, eventId: `ev_mig${migrationId}entry`, type: 'jump', params: { targetEventId: eventId(entry) } } },
    ...[...blocks.values()].flat(),
  ];
  for (const { payload } of operations) {
    if (payload.params.targetNodeId) payload.params = { targetEventId: eventId(payload.params.targetNodeId) };
    if (payload.type === 'choice') payload.params.options = payload.params.options.map(({ label, targetNodeId }) => ({ label, targetEventId: eventId(targetNodeId) }));
  }
  return { operations, warnings, nodeEventIds: Object.fromEntries([...blocks.keys()].map((id) => [id, eventId(id)])) };
}

// 选择在插入时就校验目标；先用已登记的终止哨兵暂存，再补齐完整引用。
// 单事务上限 200，每个阶段均可分批；中断时入口停在“迁移尚未完成”，不会运行半张图。
export function migrationPhases(operations) {
  const first = operations[0].payload;
  const pendingId = first.eventId.replace(/entry$/, 'pending');
  const pending = { action: 'event.insert', payload: { sceneId: first.sceneId, eventId: pendingId, type: 'ending', params: { endingId: pendingId, title: '迁移尚未完成' } } };
  const links = [];
  const inserts = operations.map((op) => {
    const { payload } = op;
    if (!['choice', 'jump'].includes(payload.type)) return op;
    links.push({ action: 'event.update', targetIds: [payload.eventId], payload: { sceneId: payload.sceneId, eventId: payload.eventId, params: payload.params } });
    const params = payload.type === 'jump'
      ? { targetEventId: pendingId }
      : { ...payload.params, options: payload.params.options.map((option) => ({ ...option, targetEventId: pendingId })) };
    return { ...op, payload: { ...payload, params } };
  });
  return [
    [pending, ...inserts],
    [...links, { action: 'event.delete', targetIds: [pendingId], payload: { sceneId: first.sceneId, eventId: pendingId } }],
  ];
}
