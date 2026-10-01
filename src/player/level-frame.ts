// HTML levels have an opaque origin. Only explicit content reads and the configured C runner cross the host bridge.
interface BridgeRequest { channel: string; kind: string; id: string; url: string; method: string; headers: [string, string][]; body?: ArrayBuffer }

export function allowedLevelRequest(url: URL, method: string, contentRoot: URL, initPayload: unknown): boolean {
  if (url.username || url.password) return false;
  let relative: string;
  try { relative = decodeURIComponent(url.pathname.slice(contentRoot.pathname.length)); } catch { return false; }
  if (url.origin === contentRoot.origin && url.pathname.startsWith(contentRoot.pathname) && /^(assets|levels)\//.test(relative) && relative.split('/').every((part) => part && !part.startsWith('.') && !/[\\:\0]/.test(part))) {
    return method === 'GET' || method === 'HEAD';
  }
  let payload = initPayload;
  if (typeof payload === 'string') { try { payload = JSON.parse(payload); } catch { return false; } }
  const runner = payload && typeof payload === 'object' ? (payload as Record<string, unknown>).runner : undefined;
  if (typeof runner !== 'string') return false;
  try {
    const base = new URL(runner);
    if (base.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(base.hostname) || base.username || base.password || base.pathname !== '/' || base.search || base.hash) return false;
    return url.origin === base.origin && !url.search && !url.hash && ((method === 'GET' && url.pathname === '/health') || (method === 'POST' && url.pathname === '/run'));
  } catch { return false; }
}

function levelBootstrap(config: { channel: string; baseUrl: string }): void {
  const pending = new Map<string, { resolve: (value: Response) => void; reject: (error: Error) => void; cleanup: () => void }>();
  window.addEventListener('message', (event: MessageEvent) => {
    const data = event.data;
    if (event.source !== window.parent || data?.channel !== config.channel || data.kind !== 'fetch-result') return;
    const request = pending.get(data.id);
    if (!request) return;
    pending.delete(data.id); request.cleanup();
    if (data.error) request.reject(new Error(data.error));
    else request.resolve(new Response([204, 205, 304].includes(data.status) ? null : data.body, { status: data.status, statusText: data.statusText, headers: data.headers }));
  });
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const inputUrl = input instanceof Request ? input.url : String(input);
    const request = new Request(new URL(inputUrl, config.baseUrl), init ?? (input instanceof Request ? input : undefined));
    if (request.signal.aborted) throw new DOMException('The request was aborted.', 'AbortError');
    const body = ['GET', 'HEAD'].includes(request.method) ? undefined : await request.arrayBuffer();
    const id = crypto.randomUUID();
    return new Promise<Response>((resolve, reject) => {
      const cancel = (): void => { pending.delete(id); cleanup(); reject(new DOMException('The request was aborted.', 'AbortError')); };
      const timer = window.setTimeout(() => { pending.delete(id); cleanup(); reject(new Error('Level request timed out')); }, 30000);
      const cleanup = (): void => { window.clearTimeout(timer); request.signal.removeEventListener('abort', cancel); };
      request.signal.addEventListener('abort', cancel, { once: true });
      pending.set(id, { resolve, reject, cleanup });
      window.parent.postMessage({ channel: config.channel, kind: 'fetch', id, url: request.url, method: request.method, headers: Array.from(request.headers.entries()), body }, '*');
    });
  };
}

export async function prepareIsolatedLevel(frame: HTMLIFrameElement, levelUrl: string, contentRoot: URL, initPayload: unknown, signal: AbortSignal): Promise<() => void> {
  frame.setAttribute('sandbox', 'allow-scripts');
  frame.referrerPolicy = 'no-referrer';
  frame.allow = 'autoplay';
  const source = await fetch(levelUrl, { signal, credentials: 'omit', redirect: 'error' });
  if (!source.ok) throw new Error(`关卡文件读取失败（HTTP ${source.status}）`);
  const html = await source.text();
  if (signal.aborted) throw new DOMException('The request was aborted.', 'AbortError');
  const channel = crypto.randomUUID();
  const requests = new Set<AbortController>();
  let documentUrl: string | null = null;
  let closed = false;
  const respond = (message: Record<string, unknown>): void => { if (!closed && !signal.aborted) frame.contentWindow?.postMessage({ channel, kind: 'fetch-result', ...message }, '*'); };
  const listener = (event: MessageEvent): void => {
    const data = event.data as BridgeRequest | undefined;
    if (closed || event.source !== frame.contentWindow || data?.channel !== channel || data.kind !== 'fetch') return;
    if (typeof data.id !== 'string' || data.id.length > 100) return;
    void (async () => {
      let controller: AbortController | undefined;
      try {
        if (typeof data.url !== 'string' || typeof data.method !== 'string' || !Array.isArray(data.headers)) throw new Error('Invalid level request');
        const url = new URL(data.url);
        if (!allowedLevelRequest(url, data.method, contentRoot, initPayload)) throw new Error('Level request is outside the authorised content or runner');
        if (requests.size >= 16 || (data.body && (!(data.body instanceof ArrayBuffer) || data.body.byteLength > 1024 * 1024))) throw new Error('Level request limit exceeded');
        controller = new AbortController(); requests.add(controller);
        const headers = new Headers();
        for (const [key, value] of data.headers) if (['accept', 'content-type', 'range'].includes(key.toLowerCase()) && typeof value === 'string') headers.set(key, value);
        const response = await fetch(url, { method: data.method, headers, body: data.body, credentials: 'omit', redirect: 'error', signal: AbortSignal.any([signal, controller.signal, AbortSignal.timeout(25000)]) });
        const body = await response.arrayBuffer();
        if (body.byteLength > 24 * 1024 * 1024) throw new Error('Level response exceeds 24 MB');
        respond({ id: data.id, status: response.status, statusText: response.statusText, headers: Array.from(response.headers.entries()), body });
      } catch (error) { respond({ id: data.id, error: error instanceof Error ? error.message : 'Level request failed' }); }
      finally { if (controller) requests.delete(controller); }
    })();
  };
  const cleanup = (): void => { closed = true; window.removeEventListener('message', listener); for (const request of requests) request.abort(); requests.clear(); if (documentUrl) URL.revokeObjectURL(documentUrl); documentUrl = null; signal.removeEventListener('abort', cleanup); };
  window.addEventListener('message', listener);
  signal.addEventListener('abort', cleanup, { once: true });
  const encoded = JSON.stringify({ channel, baseUrl: levelUrl }).replace(/</g, '\\u003c');
  const contentSource = contentRoot.href.replace(/"/g, '%22').replace(/;/g, '%3B');
  const policy = `default-src 'none'; script-src 'unsafe-inline' ${contentSource}; style-src 'unsafe-inline' ${contentSource}; img-src ${contentSource} data: blob:; media-src ${contentSource} blob:; font-src ${contentSource} data:; connect-src 'none'; object-src 'none'; frame-src 'none'; worker-src 'none'; form-action 'none'; base-uri ${contentSource}`;
  const bootstrap = `<meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${policy.replace(/&/g, '&amp;')}"><base href="${levelUrl.replace(/&/g, '&amp;').replace(/"/g, '&quot;')}"><script>(${levelBootstrap.toString()})(${encoded});<\/script>`;
  const document = /<head\b[^>]*>/i.test(html) ? html.replace(/<head\b[^>]*>/i, (head) => `${head}${bootstrap}`) : /<html\b[^>]*>/i.test(html) ? html.replace(/<html\b[^>]*>/i, (tag) => `${tag}<head>${bootstrap}</head>`) : `${bootstrap}${html}`;
  // A Blob URL retains the host URL origin for legacy postMessage targetOrigin checks.
  // The sandbox still gives the document an opaque security origin and denies parent/storage access.
  documentUrl = URL.createObjectURL(new Blob([document], { type: 'text/html;charset=utf-8' }));
  frame.removeAttribute('srcdoc');
  frame.src = documentUrl;
  return cleanup;
}
