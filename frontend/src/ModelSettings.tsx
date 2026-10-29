import {useRef, useState} from 'react';
import {useMutation, useQuery, useQueryClient} from '@tanstack/react-query';
import {Link} from 'react-router-dom';
import {client, unwrap} from './api';

export function useModelSettings(enabled=true) {
  return useQuery({queryKey:['model-settings'], enabled, queryFn:()=>unwrap(client.GET('/api/v1/account/model-settings'))});
}

export function ModelSettings() {
  const query=useModelSettings(), cache=useQueryClient();
  const form=useRef<HTMLFormElement>(null);
  const [notice,setNotice]=useState('');
  const update=async()=>{
    await Promise.all([cache.invalidateQueries({queryKey:['model-settings']}),cache.invalidateQueries({queryKey:['meta']})]);
  };
  const save=useMutation({mutationFn:async(body:{deepseek_key:string;embedding_key:string})=>{
    try { return await unwrap(client.POST('/api/v1/account/model-settings',{body})); }
    finally { body.deepseek_key=''; body.embedding_key=''; form.current?.reset(); }
  },onSuccess:async()=>{setNotice('已加密保存。尚未检测有效性，可点击下方连接检测。');await update();}});
  const remove=useMutation({mutationFn:()=>unwrap(client.DELETE('/api/v1/account/model-settings')),
    onSuccess:async result=>{setNotice(result.message);form.current?.reset();await update();}});
  const test=useMutation({mutationFn:()=>unwrap(client.POST('/api/v1/account/model-settings/test')),
    onSuccess:result=>setNotice(result.message)});
  const busy=save.isPending||remove.isPending||test.isPending;
  const failure=query.error||save.error||remove.error||test.error;
  const clear=()=>{setNotice('');save.reset();remove.reset();test.reset();};
  return <main className="library model-settings"><Link to="/">← 我的文档</Link><h1>个人模型设置</h1>
    <p>每个账号使用自己的服务额度。DeepSeek 用于修改、Agent 和按需联网搜索；硅基流动用于资料向量化与语义检索。</p>
    <p className="muted">密钥经服务端加密后保存，页面不会读回原文，也不会保存在浏览器本地存储。部署者管理服务器和加密配置，请只在你信任的站点提交密钥。</p>
    {query.data&&!query.data.required&&<p className="notice">当前是离线演示环境，不会调用真实模型。保存密钥不会切换服务运行模式。</p>}
    {query.data?.error&&<p className="error" role="alert">{query.data.error}</p>}
    <div className="panel"><h2>{query.data?.configured?'已配置个人密钥':'尚未配置'}</h2>
      <p>DeepSeek：{query.data?.deepseek_mask||'未保存'} · 模型：deepseek-flash</p>
      <p>硅基流动：{query.data?.embedding_mask||'未保存'} · 模型：BAAI/bge-m3</p>
      <p className="muted">更换密钥时请同时重新填写两项。排队中的旧任务会停止，需要重新提交；已经开始的调用可能仍会完成。</p>
      <form ref={form} autoComplete="off" onSubmit={e=>{e.preventDefault();clear();const values=new FormData(e.currentTarget);
        save.mutate({deepseek_key:String(values.get('deepseek_key')),embedding_key:String(values.get('embedding_key'))});}}>
        <label>DeepSeek API 密钥<input name="deepseek_key" type="password" autoComplete="new-password" required minLength={16} maxLength={512} spellCheck={false}/></label>
        <label>硅基流动 API 密钥<input name="embedding_key" type="password" autoComplete="new-password" required minLength={16} maxLength={512} spellCheck={false}/></label>
        <button className="primary" disabled={busy||!query.data?.storage_ready}>{save.isPending?'正在保存…':'加密保存两个密钥'}</button>
      </form>
      <div className="actions"><button disabled={busy||!query.data?.configured} onClick={()=>{clear();test.mutate();}}>检测连接（会有极少量向量调用）</button>
      <button disabled={busy||!query.data?.configured} onClick={()=>{if(window.confirm('确定删除本账号两个密钥？后续 AI 任务将不可用，已有文档不会删除。')){clear();remove.mutate();}}}>删除两个密钥</button></div>
      <p className="muted">连接检测只检查 DeepSeek 身份认证和一次短文本向量请求，不代表搜索权限、余额及全部模型功能均可用。</p>
      {test.isPending&&<p role="status">正在检测两个服务…</p>}
      {failure&&<p className="error" role="alert">{failure.message}</p>}
      {notice&&<p className="notice" role="status">{notice}</p>}
    </div>
  </main>;
}
