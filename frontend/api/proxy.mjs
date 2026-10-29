// Vercel仅转发短HTTP请求；模型任务和数据库留在本机。禁止日志记录请求体、Cookie或密钥。
const LIMIT = 2_800_000;
const methods = new Set(['GET', 'HEAD', 'POST', 'PATCH', 'DELETE', 'OPTIONS']);
function failure(status, detail) {
  return Response.json({detail}, {status, headers: {'Cache-Control':'private, no-store','Retry-After':'5'}});
}

export async function forward(request, {origin = process.env.WRITING_BACKEND_ORIGIN, fetcher = fetch} = {}) {
  if (!methods.has(request.method)) return failure(405, '不支持此操作');
  let backend;
  try {
    backend = new URL(origin);
    if (backend.protocol !== 'https:' || backend.username || backend.password || backend.pathname !== '/' || backend.search || backend.hash)
      throw new Error('origin');
  } catch { return failure(503, '后端地址尚未配置，请联系部署者设置 WRITING_BACKEND_ORIGIN'); }
  const incoming = new URL(request.url);
  const path = incoming.pathname.startsWith('/api/v1/') ? incoming.pathname.slice(8) : incoming.searchParams.get('__path');
  if (!path || path.length > 300 || !/^[a-zA-Z0-9_-]+(?:\/[a-zA-Z0-9_-]+)*$/.test(path)) return failure(404, '接口不存在');
  if (path.endsWith('/events')) return failure(400, '此部署使用轮询更新任务进度');
  const target = new URL('/api/v1/' + path, backend);
  incoming.searchParams.delete('__path');
  target.search = incoming.searchParams.toString();
  const headers = new Headers();
  for (const name of ['content-type','x-csrf-token','idempotency-key']) {
    if (request.headers.has(name)) headers.set(name, request.headers.get(name));
  }
  if (request.headers.has('origin')) headers.set('origin', request.headers.get('origin'));
  const cookies = (request.headers.get('cookie') || '').split(';').map(c=>c.trim()).filter(c=>/^writing_(session|csrf)=/.test(c));
  if (cookies.length) headers.set('cookie', cookies.join('; '));
  let body;
  if (!['GET','HEAD'].includes(request.method)) {
    if (Number(request.headers.get('content-length')) > LIMIT) return failure(413, '上传内容过大');
    const chunks = []; let size = 0;
    if (request.body) {
      const reader = request.body.getReader();
      try {
        while (true) {
          const {value, done} = await reader.read(); if (done) break;
          size += value.byteLength;
          if (size > LIMIT) { await reader.cancel(); return failure(413, '上传内容过大'); }
          chunks.push(value);
        }
      } finally { reader.releaseLock(); }
    }
    body = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
  }
  try {
    const upstream = await fetcher(target, {method:request.method, headers, body, redirect:'manual', signal:AbortSignal.timeout(30_000)});
    // 隧道中断可能返回HTML错误页，不能把它误认为登录/API结果。
    if ((upstream.status >= 300 && upstream.status < 400) || (upstream.headers.get('content-type') || '').includes('text/html'))
      return failure(503, '后端或隧道暂时离线，请联系部署者确认电脑、Docker 和隧道仍在运行');
    const output = new Headers({'Cache-Control':'private, no-store', 'CDN-Cache-Control':'no-store','Vercel-CDN-Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});
    for (const name of ['content-type','content-disposition','x-request-id','retry-after']) {
      if (upstream.headers.has(name)) output.set(name, upstream.headers.get(name));
    }
    for (const cookie of upstream.headers.getSetCookie()) {
      // 浏览器只向Vercel同源发送会话；公网出口始终使用Secure，不透出隧道Domain。
      if (/^writing_(session|csrf)=/.test(cookie)) {
        const cleaned = cookie.replace(/;\s*Domain=[^;]*/ig, '');
        output.append('Set-Cookie', /;\s*Secure(?:;|$)/i.test(cleaned) ? cleaned : cleaned + '; Secure');
      }
    }
    return new Response(['HEAD','OPTIONS'].includes(request.method) || [204,304].includes(upstream.status) ? null : upstream.body,
      {status:upstream.status, headers:output});
  } catch {
    return failure(503, '后端连接中断或超时，请稍后重试；部署者需保持电脑、Docker 和隧道运行');
  }
}

export default {fetch: request => forward(request)};
