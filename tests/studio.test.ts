import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { advanceTo, inspectProject, parseProject } from '../src/studio/model';
const source = readFileSync(new URL('../public/studio/project.json', import.meta.url), 'utf8');
describe('studio project contract', () => {
  it('loads the shipped project and follows both story branches', () => {
    const p = parseProject(source);
    expect(inspectProject(p)).toEqual([]);
    expect(advanceTo(p, 'arrival')).toBe('lantern');
    expect(advanceTo(p, 'promise')).toBeNull();
    expect(advanceTo(p, 'promise', 0)).toBe('together');
    expect(advanceTo(p, 'promise', 1)).toBe('memory');
    expect(advanceTo(p, 'star-map')).toBeNull();
    expect(advanceTo(p, 'ending')).toBeNull();
  });
  it('rejects missing destinations and duplicate IDs before importing', () => {
    const p = parseProject(source); p.nodes[0].next = 'missing';
    expect(() => parseProject(JSON.stringify(p))).toThrow('下一段剧情不存在');
    p.nodes[0].next = 'lantern'; p.nodes[1].id = p.nodes[0].id;
    expect(() => parseProject(JSON.stringify(p))).toThrow('节点编号缺失或重复');
  });
  it('rejects external resources, invalid media kinds and arbitrary HTML plugins', () => {
    const p = parseProject(source); p.assets[0].src = 'https://example.com/tracker.png';
    expect(inspectProject(p).length).toBeGreaterThan(0);
    const q = parseProject(source); q.nodes[0].background = 'li';
    expect(inspectProject(q).some(i => i.path.endsWith('background'))).toBe(true);
    const r = parseProject(source); r.nodes.find(n => n.type === 'level')!.level = '../../arbitrary';
    expect(inspectProject(r).some(i => i.path.endsWith('level'))).toBe(true);
  });
  it('rejects malformed input and preserves the original project', () => {
    const p = parseProject(source); const copy = JSON.stringify(p);
    expect(() => parseProject('{')).toThrow('有效的 JSON');
    expect(() => parseProject('{"version":2}')).toThrow();
    expect(JSON.stringify(p)).toBe(copy);
  });
});
