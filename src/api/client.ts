// 与本地 Python 后端统一通信的客户端。
// 401 时派发 platform-session-expired 事件，由认证层（App.vue）重置会话。
export const SESSION_EXPIRED_EVENT = 'platform-session-expired';

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function dispatchSessionExpired(): void {
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}

export function onSessionExpired(handler: () => void): () => void {
  window.addEventListener(SESSION_EXPIRED_EVENT, handler);
  return () => window.removeEventListener(SESSION_EXPIRED_EVENT, handler);
}

async function request(url: string, body?: unknown): Promise<any> {
  const response = await fetch(
    url,
    body === undefined
      ? { cache: 'no-store' }
      : {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        },
  );
  const data = await response
    .json()
    .catch(() => ({ error: '服务未返回有效结果，请确认服务已启动。' }));
  if (!response.ok) {
    if (response.status === 401) dispatchSessionExpired();
    throw new ApiError(data.error || '操作未完成。', response.status);
  }
  return data;
}

/** /api 前缀调用（工作台与认证页共用） */
export function api(path: string, body?: unknown): Promise<any> {
  return request('/api' + path, body);
}

/** /api 前缀 POST（空 body 也要显式传 {}） */
export function apiPost(path: string, body: unknown = {}): Promise<any> {
  return request('/api' + path, body);
}

/** /api 前缀 DELETE */
export async function apiDelete(path: string): Promise<any> {
  const response = await fetch('/api' + path, { method: 'DELETE', cache: 'no-store' });
  const data = await response
    .json()
    .catch(() => ({ error: '服务未返回有效结果。' }));
  if (!response.ok) {
    if (response.status === 401) dispatchSessionExpired();
    throw new ApiError(data.error || '删除未完成。', response.status);
  }
  return data;
}

/** 直接 URL 调用（平台页：/api/platform、/api/packages、/api/library 等） */
export function platformRequest(url: string, body?: unknown): Promise<any> {
  return request(url, body);
}

/** DELETE 请求（无 body，如删除资料资产） */
export async function platformDelete(url: string): Promise<any> {
  const response = await fetch(url, { method: 'DELETE', cache: 'no-store' });
  const data = await response
    .json()
    .catch(() => ({ error: '服务未返回有效结果。' }));
  if (!response.ok) {
    if (response.status === 401) dispatchSessionExpired();
    throw new ApiError(data.error || '删除未完成。', response.status);
  }
  return data;
}
