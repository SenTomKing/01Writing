import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { client, unwrap } from './api';

type Entry = {id:string; kind:string; title:string; content:string; expires_at:string|null};
export function KnowledgePanel({selected,onChange}:{selected:string[];onChange:(ids:string[])=>void}) {
  const queries=useQueryClient();
  const editor=useRef<HTMLDetailsElement>(null);
  const entries=useQuery({queryKey:['knowledge'],queryFn:()=>unwrap(client.GET('/api/v1/knowledge'))});
  const [kind,setKind]=useState<'source'|'memory'>('source');
  const [title,setTitle]=useState(''),[content,setContent]=useState(''),[editing,setEditing]=useState('');
  const [expires,setExpires]=useState(''),[fileError,setFileError]=useState('');
  const reset=()=>{setTitle('');setContent('');setEditing('');setExpires('');};
  const save=useMutation({mutationFn:()=>{
    const body={kind,title,content,expires_at:expires?new Date(expires).toISOString():null};
    return editing?unwrap(client.PATCH('/api/v1/knowledge/{entry_id}',{params:{path:{entry_id:editing}},body})):
      unwrap(client.POST('/api/v1/knowledge',{body}));
  },onSuccess:()=>{reset();queries.invalidateQueries({queryKey:['knowledge']});}});
  const remove=useMutation({mutationFn:(id:string)=>unwrap(client.DELETE('/api/v1/knowledge/{entry_id}',{params:{path:{entry_id:id}}})),
    onSuccess:(_,id)=>{onChange(selected.filter(value=>value!==id));if(editing===id)reset();queries.invalidateQueries({queryKey:['knowledge']});}});
  const edit=(entry:Entry)=>{if(editor.current)editor.current.open=true;setEditing(entry.id);setKind(entry.kind as 'source'|'memory');setTitle(entry.title);setContent(entry.content);
    setExpires(entry.expires_at?new Date(new Date(entry.expires_at).getTime()-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16):'');};
  const error=entries.error||save.error||remove.error;
  return <section className="panel knowledge-panel"><div className="panel-heading"><h2>参考资料与写作记忆</h2><span>本次已选择 {selected.length} 项</span></div>
    <p className="muted">勾选本次允许使用的资料与偏好。资料内容不会自动成为指令；候选引用仍需你核对。记忆由你明确保存，可修改、设置有效期和删除。</p>
    {error&&<p role="alert" className="error">{error instanceof Error?error.message:'读取失败'}</p>}
    <div className="knowledge-list">{entries.data?.map(entry=>{
      const expired=!!entry.expires_at&&new Date(entry.expires_at)<=new Date();
      return <div className="knowledge-row" key={entry.id}><label><input type="checkbox" checked={selected.includes(entry.id)} disabled={expired&&!selected.includes(entry.id)}
        onChange={event=>onChange(event.target.checked?[...selected,entry.id]:selected.filter(id=>id!==entry.id))}/>
        {entry.kind==='source'?'资料':'记忆'} · {entry.title}{expired?'（已过期）':''}</label>
        <details><summary>查看内容</summary><pre>{entry.content}</pre></details>
        <button onClick={()=>edit(entry)}>编辑</button> <button disabled={remove.isPending} onClick={()=>{if(window.confirm('删除后未来任务不再使用。已创建的任务仍保留当时的依据，确定删除？'))remove.mutate(entry.id);}}>删除</button></div>;
    })}</div>
    <details ref={editor}><summary>{editing?'正在编辑：'+title:'添加资料或记忆'}</summary>
      <form onSubmit={event=>{event.preventDefault();save.mutate();}}>
        <label>类型<select value={kind} disabled={!!editing} onChange={event=>setKind(event.target.value as 'source'|'memory')}><option value="source">参考资料</option><option value="memory">长期偏好或术语</option></select></label>
        <label>标题<input required maxLength={200} value={title} onChange={event=>setTitle(event.target.value)}/></label>
        <label>内容<textarea required rows={5} maxLength={kind==='memory'?2000:20000} value={content} onChange={event=>setContent(event.target.value)}/></label>
        <label>导入纯文本资料（TXT / Markdown，最多 20,000 字符）<input type="file" accept=".txt,.md,text/plain,text/markdown" onChange={async event=>{
          const file=event.target.files?.[0];if(!file)return;
          try{if(file.size>100000)throw new Error('文件过大，请选择不超过 100 KB 的文本');
            if(!/\.(txt|md)$/i.test(file.name))throw new Error('目前支持 TXT 和 Markdown，PDF 请先提取文字');
            const text=await file.text();if(text.length>20000)throw new Error('文字超过 20,000 字符，请拆分资料');
            setContent(text);setTitle(file.name);setKind('source');setEditing('');setFileError('');
          }catch(error){setFileError(error instanceof Error?error.message:'导入失败');}
        }}/></label>
        {fileError&&<p role="alert" className="error">{fileError}</p>}
        <label>有效期（可不填）<input type="datetime-local" value={expires} onChange={event=>setExpires(event.target.value)}/></label>
        <button className="primary" disabled={save.isPending}>保存{kind==='memory'?'记忆':'资料'}</button> <button type="button" onClick={reset}>清空编辑</button>
      </form>
    </details>
  </section>;
}
