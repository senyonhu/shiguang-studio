import { describe, expect, it } from 'vitest';
import { allowedLevelRequest } from '../../../src/player/level-frame.ts';

describe('isolated level request policy', () => {
  const root = new URL('http://127.0.0.1:5199/content/');
  const payload = JSON.stringify({ runner: 'http://127.0.0.1:5201' });
  it('supports relative content and the explicitly configured C runner without granting editor access', () => {
    expect(allowedLevelRequest(new URL('levels/data.json', root), 'GET', root, payload)).toBe(true);
    expect(allowedLevelRequest(new URL('http://127.0.0.1:5201/run'), 'POST', root, payload)).toBe(true);
    expect(allowedLevelRequest(new URL('http://127.0.0.1:5201/health'), 'GET', root, payload)).toBe(true);
    for (const url of ['http://127.0.0.1:5199/v1/ui-session', 'http://127.0.0.1:5201/other', 'http://127.0.0.1:5202/run', 'https://example.com/run', 'http://127.0.0.1:5199/content/levels/%2e%2e%2fproject.json', 'http://127.0.0.1:5199/content/levels/%5csecret']) {
      expect(allowedLevelRequest(new URL(url), 'POST', root, payload), url).toBe(false);
    }
    expect(allowedLevelRequest(new URL('levels/data.json', root), 'POST', root, payload)).toBe(false);
    expect(allowedLevelRequest(new URL('http://user:password@127.0.0.1:5201/run'), 'POST', root, payload)).toBe(false);
    expect(allowedLevelRequest(new URL('http://127.0.0.1:5201/run'), 'POST', root, '{}')).toBe(false);
  });
});
