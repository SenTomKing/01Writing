import test from 'node:test';
import assert from 'node:assert/strict';
import {forward} from '../api/proxy.mjs';
const origin = 'https://test.trycloudflare.com';
const req = (path, init={}) => new Request('https://writing.vercel.app'+path, init);
test('未配置地址、非法路径、SSE都不发出请求', async()=>{
  for(const [request, upstream, expected] of [
    [req('/api/v1/meta'),undefined,503], [req('/api/v1/meta'),'http://localhost:8000',503],
    [req('/api/proxy?__path=..%2fadmin'),origin,404], [req('/api/v1/tasks/one/events'),origin,400]]) {
    const response=await forward(request,{origin:upstream,fetcher:()=>assert.fail('不得联网')});
    assert.equal(response.status,expected);
  }
});
test('转发正确路径查询、CSRF和会话，过滤其他网站Cookie', async()=>{
  const response=await forward(req('/api/proxy?__path=documents&page=2',{method:'POST',body:'{"title":"示例"}',headers:{
    'content-type':'application/json','cookie':'writing_session=one; _vercel_jwt=private; writing_csrf=two','x-csrf-token':'two','idempotency-key':'unique','origin':'https://writing.vercel.app'}}),
    {origin,fetcher:async(url,options)=>{
      assert.equal(url.href,origin+'/api/v1/documents?page=2');assert.equal(options.redirect,'manual');
      assert.equal(options.headers.get('cookie'),'writing_session=one; writing_csrf=two');
      assert.equal(options.headers.get('x-csrf-token'),'two');assert.equal(options.headers.get('origin'),'https://writing.vercel.app');
      assert.equal(new TextDecoder().decode(options.body),'{"title":"示例"}');
      const headers=new Headers({'content-type':'application/json'});
      headers.append('set-cookie','writing_session=one; Path=/; HttpOnly; SameSite=lax; Domain=test.trycloudflare.com');
      headers.append('set-cookie','writing_csrf=two; Path=/; SameSite=lax');
      return new Response('{"id":"ok"}',{status:201,headers});
    }});
  assert.equal(response.status,201);assert.equal(response.headers.get('cache-control'),'private, no-store');
  assert.equal(response.headers.getSetCookie().length,2);
  for(const cookie of response.headers.getSetCookie()){assert.match(cookie,/Secure/);assert.doesNotMatch(cookie,/Domain=/);}
  assert.deepEqual(await response.json(),{id:'ok'});
});
test('网络中断、隧道HTML、重定向变为明确的离线提示',async()=>{
  for(const fetcher of [()=>{throw Error('secret internal hostname');},()=>new Response('<html>offline</html>',{status:502,headers:{'content-type':'text/html'}}),()=>new Response('',{status:302,headers:{location:'https://other.example'}})]) {
    const response=await forward(req('/api/v1/auth/me'),{origin,fetcher});
    assert.equal(response.status,503);assert.doesNotMatch(await response.text(),/secret|other.example/);
  }
});
test('超大上传在转发前拒绝，登录错误仍保留401',async()=>{
  const large=await forward(req('/api/v1/research/imports',{method:'POST',body:'a'.repeat(2_800_001)}),{origin,fetcher:()=>assert.fail()});
  assert.equal(large.status,413);
  const denied=await forward(req('/api/v1/auth/login'),{origin,fetcher:()=>Response.json({detail:'密码不正确'},{status:401})});
  assert.equal(denied.status,401);assert.deepEqual(await denied.json(),{detail:'密码不正确'});
});
