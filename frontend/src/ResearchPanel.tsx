import {useEffect,useState} from 'react';
import {useMutation,useQuery,useQueryClient} from '@tanstack/react-query';
import {client,unwrap} from './api';

export function ResearchPanel(){
  const queries=useQueryClient();
  const [url,setUrl]=useState(''),[title,setTitle]=useState(''),[error,setError]=useState('');
  const jobs=useQuery({queryKey:['imports'],queryFn:()=>unwrap(client.GET('/api/v1/research/imports')),refetchInterval:q=>q.state.data?.some(j=>['queued','running'].includes(j.status))?1500:false});
  const meta=useQuery({queryKey:['meta'],queryFn:()=>unwrap(client.GET('/api/v1/meta'))});
  const finished=jobs.data?.filter(j=>j.status==='succeeded').map(j=>j.id).join(',');
  useEffect(()=>{queries.invalidateQueries({queryKey:['knowledge']});},[finished,queries]);
  const submit=useMutation({mutationFn:async(body:{kind:'file'|'url';title:string;url?:string;filename?:string;content_base64?:string})=>unwrap(client.POST('/api/v1/research/imports',{body:{url:'',filename:'',content_base64:'',knowledge_id:'',...body}})),
    onSuccess:()=>{setError('');setUrl('');setTitle('');queries.invalidateQueries({queryKey:['imports']});}});
  const cancel=useMutation({mutationFn:(id:string)=>unwrap(client.POST('/api/v1/research/imports/{job_id}/cancel',{params:{path:{job_id:id}}})),onSuccess:()=>queries.invalidateQueries({queryKey:['imports']})});
  const upload=async(file?:File)=>{
    if(!file)return;
    try{
      if(file.size>2_000_000||! /\.(txt|md|pdf)$/i.test(file.name))throw new Error('请选择2MB以内的TXT、Markdown或文字PDF');
      const bytes=new Uint8Array(await file.arrayBuffer());let binary='';
      for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
      submit.mutate({kind:'file',title:file.name.slice(0,200),filename:file.name,content_base64:btoa(binary)});
    }catch(e){setError(e instanceof Error?e.message:'读取文件失败');}
  };
  const failure=error||submit.error?.message||cancel.error?.message||jobs.error?.message;
  return <div className="research-import"><h3>导入研究资料</h3>
    <p className="muted">资料先解析、索引，再供你勾选使用。最多2MB、80页或100,000字符；暂不识别扫描件。网页正文只保存快照，不执行脚本。</p>
    <p className="muted">联网搜索：{meta.data?.web_search_available?'已配置，需逐任务授权':'未配置或不可用，请检查个人模型设置'} · 语义服务：{meta.data?.semantic_search_available?'已配置，新导入或重建后使用':'未配置；真实模式须先保存个人密钥，演示模式使用关键词'}</p>
    <label>导入文件<input type="file" accept=".txt,.md,.pdf" disabled={submit.isPending} onChange={e=>{upload(e.target.files?.[0]);e.target.value='';}}/></label>
    <form onSubmit={e=>{e.preventDefault();submit.mutate({kind:'url',title:title.trim()||'网页资料',url});}}>
      <label>网页资料标题<input value={title} maxLength={200} onChange={e=>setTitle(e.target.value)}/></label>
      <label>网页链接<input type="url" required value={url} maxLength={2000} placeholder="https://…" onChange={e=>setUrl(e.target.value)}/></label>
      <button disabled={submit.isPending}>抓取并保存网页</button>
    </form>
    {failure&&<p className="error" role="alert">{failure}</p>}
    {!!jobs.data?.length&&<details open><summary>最近的资料导入</summary>{jobs.data.map(job=><div className="import-row" key={job.id}>
      <strong>{job.title}</strong><span>{({queued:'排队中',running:'处理中',succeeded:'完成',failed:'失败',cancelled:'已取消'} as Record<string,string>)[job.status]}</span>
      <p>{job.error||job.progress}</p>{['queued','running'].includes(job.status)&&<button disabled={cancel.isPending} onClick={()=>cancel.mutate(job.id)}>取消导入</button>}
    </div>)}</details>}
  </div>;
}

export function EvidenceAudit({report}:{report:Record<string,unknown>}){
  if(!report.status)return null;
  const findings=Array.isArray(report.findings)?report.findings as Array<Record<string,unknown>>:[];
  return <section className="evidence-audit"><h3>证据核对</h3><p className="muted">{String(report.warning||'请结合原始资料核对。')}</p>
    {findings.map((f,i)=><div key={i} className={`finding ${String(f.verdict)}`}>
      <strong>{({supported:'资料支持',contradicted:'与资料矛盾',insufficient:'依据不足'} as Record<string,string>)[String(f.verdict)]||'待核对'}</strong>
      <p>待核对陈述：{String(f.claim)}</p><p>{String(f.explanation)}</p>
      {!!f.quote&&<blockquote>{String(f.quote)} <small>〔{String(f.ref)}{f.page?`，第${f.page}页`:''}〕</small></blockquote>}
      {typeof f.url==='string'&&/^https?:\/\//i.test(f.url)&&<a href={f.url} target="_blank" rel="noopener noreferrer">打开来源</a>}
    </div>)}
  </section>;
}
