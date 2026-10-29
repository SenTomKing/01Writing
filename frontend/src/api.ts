import createClient from 'openapi-fetch';
import type { paths, components } from './generated/api';

export type Doc = components['schemas']['DocumentOut'];
export type Task = components['schemas']['TaskOut'];
export type Version = components['schemas']['VersionOut'];
export type Me = components['schemas']['UserOut'];

export const client = createClient<paths>({ credentials: 'include', fetch: async request => {
  try { return await fetch(request); }
  catch { throw new Error('暂时无法连接后端，请确认部署者电脑、Docker 和隧道仍在运行'); }
} });
let csrf = '';
export function setCsrf(value: string) { csrf = value; }
client.use({ onRequest({request}) {
  if (!['GET', 'HEAD'].includes(request.method)) request.headers.set('X-CSRF-Token', csrf);
  return request;
}});
export async function unwrap<T>(promise: Promise<{data?: T; error?: unknown; response: Response}>): Promise<T> {
  const {data, error, response} = await promise;
  if (!response.ok) {
    const detail = (error as {detail?: unknown})?.detail;
    throw new Error(typeof detail === 'string' ? detail : response.status === 422 ? '请检查输入内容、长度和格式' : '请求失败，请稍后重试');
  }
  return data as T;
}
export const docPath = (id: string) => ({ path: { document_id: id } });
export const taskPath = (id: string) => ({ path: { task_id: id } });
export const freshKey = () => ({ 'Idempotency-Key': crypto.randomUUID() });
