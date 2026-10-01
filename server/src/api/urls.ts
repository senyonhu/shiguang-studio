// 服务地址工具：统一生成 127.0.0.1 形式的 URL，避免出现 0.0.0.0 监听地址。
export function serviceUrl(host: string, port: number): string {
  const normalized = host === '0.0.0.0' || host === '::' ? '127.0.0.1' : host;
  return `http://${normalized}:${port}`;
}

export function joinUrl(base: string, pathname: string): string {
  return `${base.replace(/\/$/, '')}${pathname.startsWith('/') ? pathname : `/${pathname}`}`;
}
