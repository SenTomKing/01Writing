import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, Navigate, Route, Routes, useNavigate, useParams } from 'react-router-dom';
import { diffChars } from 'diff';
import { client, docPath, freshKey, setCsrf, taskPath, unwrap, type Doc, type Me, type Task } from './api';

const labels: Record<Task['status'], string> = {queued:'等待执行', running:'正在生成', review:'等待确认', accepted:'已接受', rejected:'已拒绝', failed:'生成失败', cancelled:'已取消'};
const kinds: Record<string,string> = {initial:'初始正文', edit:'手动保存', accepted:'接受修订', restored:'恢复历史', imported:'导入版本'};
const stamp = (value: string) => new Date(value).toLocaleString('zh-CN', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'});
const message = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试';

export default function App() {
  const queries = useQueryClient();
  const me = useQuery<Me | null>({queryKey:['me'], queryFn: async () => {
    const result = await client.GET('/api/v1/auth/me');
    if (result.response.status === 401) return null;
    if (!result.response.ok || !result.data) throw new Error('服务暂时不可用，请检查后端是否启动');
    setCsrf(result.data.csrf_token); return result.data;
  }});
  const meta = useQuery({queryKey:['meta'], queryFn:()=>unwrap(client.GET('/api/v1/meta'))});
  const [exitError, setExitError] = useState('');
  const logout = useMutation({mutationFn:()=>unwrap(client.POST('/api/v1/auth/logout')), onSuccess:()=>{
    setCsrf(''); queries.clear(); queries.setQueryData(['me'], null);
  }, onError: error=>setExitError(message(error))});
  if (me.isPending) return <div className="loading">正在打开工作台…</div>;
  if (me.error) return <main className="login-card"><h1>连接暂时中断</h1><p>{message(me.error)}</p><button onClick={()=>me.refetch()}>重新连接</button></main>;
  if (!me.data) return <Login onLogin={user=>{setCsrf(user.csrf_token);queries.clear();queries.setQueryData(['me'],user);}}/>;
  return <div className="app-shell">
    <header className="topbar"><Link to="/" className="brand"><span className="brand-symbol">W</span>01Writing<span className="brand-note">写作工作台</span></Link>
      <div className="account"><span className="mode">{meta.data?.provider === 'demo' ? '离线演示' : 'DeepSeek 已接入'}</span><span>{me.data.username}</span><button className="quiet" disabled={logout.isPending} onClick={()=>logout.mutate()}>退出</button></div></header>
    {exitError && <div role="alert" className="error">{exitError}</div>}
    <Routes><Route path="/" element={<Library/>}/><Route path="/documents/:id" element={<Workbench/>}/><Route path="*" element={<Navigate to="/" replace/>}/></Routes>
    <footer className="footer">01Writing 1.0 <span>每次修改都有来处，每个版本都可回看。</span></footer>
  </div>;
}

function Login({onLogin}:{onLogin:(user:Me)=>void}) {
  const [register,setRegister] = useState(false);
  const mutation = useMutation({mutationFn:({username,password}:{username:string;password:string})=>
    register ? unwrap(client.POST('/api/v1/auth/register',{body:{username,password}})) : unwrap(client.POST('/api/v1/auth/login',{body:{username,password}})), onSuccess:onLogin});
  return <main className="landing"><section className="intro"><div className="eyebrow">01WRITING / YOUR WRITING SPACE</div><h1>把想法写下来，<br/>把修改留给选择。</h1><p>保留原意，比较建议，在你确认之后形成新版本。<br/>从第一稿，到更好的下一稿。</p><div className="intro-features"><span>01 文档与版本</span><span>02 AI 修改建议</span><span>03 人工确认</span></div></section>
    <section className="login-card"><span className="brand-symbol">W</span><h2>{register?'建立你的写作空间':'欢迎回来'}</h2><p className="muted">{register?'创建账户，开始保存你的文档。':'登录后继续上次的思考。'}</p>
    <form onSubmit={e=>{e.preventDefault();const values=new FormData(e.currentTarget);mutation.mutate({username:String(values.get('username')),password:String(values.get('password'))});}}>
      <label>用户名<input name="username" autoComplete="username" required pattern="[a-z0-9_]{3,40}" placeholder="3–40 位小写字母、数字或下划线"/></label>
      <label>密码<input name="password" type="password" autoComplete={register?'new-password':'current-password'} minLength={12} maxLength={128} required placeholder="至少 12 个字符"/></label>
      {mutation.error && <p role="alert" className="error">{message(mutation.error)}</p>}
      <button className="primary full" disabled={mutation.isPending}>{mutation.isPending?'正在处理…':register?'创建账户':'登录工作台'}</button>
    </form><p className="switch">{register?'已有账户？':'第一次使用？'}<button className="link" onClick={()=>{setRegister(!register);mutation.reset();}}>{register?'去登录':'创建账户'}</button></p></section></main>;
}

function Library() {
  const queries=useQueryClient(), navigate=useNavigate();
  const [show,setShow]=useState(false), [page,setPage]=useState(1);
  const docs=useQuery({queryKey:['documents',page],queryFn:()=>unwrap(client.GET('/api/v1/documents',{params:{query:{page,limit:30}}}))});
  const create=useMutation({mutationFn:(body:{title:string;content:string})=>unwrap(client.POST('/api/v1/documents',{body})),onSuccess:doc=>{queries.invalidateQueries({queryKey:['documents']});navigate(`/documents/${doc.id}`);}});
  return <main className="library"><div className="page-heading"><div><div className="eyebrow">WORKSPACE</div><h1>我的文档</h1><p className="muted">从一段文字开始，保留每一次重要修改。</p></div><button className="primary" onClick={()=>setShow(true)}>＋ 新建文档</button></div>
    {docs.error && <p role="alert" className="error">{message(docs.error)}</p>}
    {docs.isPending?<p>正在读取文档…</p>:docs.data?.items.length===0?<div className="empty"><div className="paper-icon">≡</div><h2>这里是下一稿的起点</h2><p>新建文档，写下正文，再让 AI 给出修改建议。</p><button className="primary" onClick={()=>setShow(true)}>开始第一篇文档</button></div>:
    <div className="document-grid">{docs.data?.items.map(doc=><Link className="document-card" to={`/documents/${doc.id}`} key={doc.id}><span className="doc-mark">文档 / {String(doc.current_version.number).padStart(2,'0')}</span><h2>{doc.title}</h2><p>{doc.current_version.content.slice(0,150)}</p><div><span>{stamp(doc.updated_at)}</span><span>打开文档 ↗</span></div></Link>)}</div>}
    {docs.data && docs.data.total>30 && <div className="pagination"><button disabled={page===1} onClick={()=>setPage(page-1)}>上一页</button><span>第 {page} 页</span><button disabled={page*30>=docs.data.total} onClick={()=>setPage(page+1)}>下一页</button></div>}
    {show && <div className="overlay"><section role="dialog" aria-modal="true" aria-label="新建文档" className="dialog"><h2>新建文档</h2><form onSubmit={e=>{e.preventDefault();const values=new FormData(e.currentTarget);create.mutate({title:String(values.get('title')),content:String(values.get('content'))});}}>
      <label>文档标题<input name="title" maxLength={200} required autoFocus placeholder="给这篇文字起个名字"/></label><label>初始正文<textarea name="content" rows={8} maxLength={20000} required placeholder="粘贴或写下你的原稿…"/></label>
      {create.error&&<p role="alert" className="error">{message(create.error)}</p>}<div className="actions"><button type="button" onClick={()=>setShow(false)}>取消</button><button className="primary" disabled={create.isPending}>创建文档</button></div></form></section></div>}
  </main>;
}

function Workbench() {
  const {id=''}=useParams();
  const doc=useQuery({queryKey:['document',id],queryFn:()=>unwrap(client.GET('/api/v1/documents/{document_id}',{params:docPath(id)}))});
  if(doc.isPending)return <div className="loading">正在打开文档…</div>;
  if(doc.error||!doc.data)return <main className="library"><Link to="/">← 返回文档</Link><p role="alert" className="error">{message(doc.error)}</p></main>;
  return <Editor key={id} doc={doc.data}/>;
}

function Editor({doc}:{doc:Doc}) {
  const queries=useQueryClient();
  const [draft,setDraft]=useState(doc.current_version.content),[base,setBase]=useState(doc.current_version_id),[saved,setSaved]=useState(doc.current_version.content);
  const [title,setTitle]=useState(doc.title),[instruction,setInstruction]=useState('保持原意，让表达更简洁自然'),[feedback,setFeedback]=useState('');
  const [selected,setSelected]=useState(''),[historyPage,setHistoryPage]=useState(1),[taskPage,setTaskPage]=useState(1),[notice,setNotice]=useState('');
  const dirty=draft!==saved;
  const versions=useQuery({queryKey:['versions',doc.id,historyPage],queryFn:()=>unwrap(client.GET('/api/v1/documents/{document_id}/versions',{params:{path:{document_id:doc.id},query:{page:historyPage,limit:30}}}))});
  const tasks=useQuery({queryKey:['tasks',doc.id,taskPage],queryFn:()=>unwrap(client.GET('/api/v1/documents/{document_id}/tasks',{params:{path:{document_id:doc.id},query:{page:taskPage}}})),refetchInterval:query=>query.state.data?.some(t=>['queued','running'].includes(t.status))?1500:false});
  const taskId=selected||tasks.data?.[0]?.id||'';
  const task=useQuery({queryKey:['task',taskId],enabled:!!taskId,queryFn:()=>unwrap(client.GET('/api/v1/tasks/{task_id}',{params:taskPath(taskId)})),refetchInterval:query=>query.state.data&&['queued','running'].includes(query.state.data.status)?1500:false});
  const active=task.data;
  useEffect(()=>{if(base!==doc.current_version_id&&!dirty){setDraft(doc.current_version.content);setSaved(doc.current_version.content);setBase(doc.current_version_id);}},[doc.current_version_id,doc.current_version.content,base,dirty]);
  useEffect(()=>{
    if(!active||!['queued','running'].includes(active.status))return;
    const events=new EventSource(`/api/v1/tasks/${active.id}/events`,{withCredentials:true});
    events.addEventListener('progress',()=>queries.invalidateQueries({queryKey:['task',active.id]}));
    events.onerror=()=>events.close();
    return ()=>events.close();
  },[active?.id,active?.status,queries]);
  const action=useMutation({mutationFn:(work:()=>Promise<unknown>)=>work(),onSuccess:async()=>{
    setNotice('操作已保存');
    await Promise.all(['document','documents','versions','tasks','task'].map(key=>queries.invalidateQueries({queryKey:key==='documents'?[key]:key==='task'?[key]:[key,doc.id]})));
  }});
  const perform=(work:()=>Promise<unknown>)=>{setNotice('');action.mutate(work);};
  const submitTask=()=>perform(async()=>{const result=await unwrap(client.POST('/api/v1/documents/{document_id}/tasks',{params:{...docPath(doc.id),header:freshKey()},body:{instruction,base_version_id:base}}));setSelected(result.id);});
  const retry=()=>perform(async()=>{const result=await unwrap(client.POST('/api/v1/tasks/{task_id}/retry',{params:{...taskPath(taskId),header:freshKey()}}));setSelected(result.id);setFeedback('');});
  const restore=(versionId:string)=>perform(async()=>{
    const result=await unwrap(client.POST('/api/v1/documents/{document_id}/versions/{version_id}/restore',{params:{path:{document_id:doc.id,version_id:versionId}},body:{base_version_id:doc.current_version_id}}));
    setDraft(result.current_version.content);setSaved(result.current_version.content);setBase(result.current_version_id);
  });
  return <main className="workbench"><div className="workbench-heading"><Link to="/" className="back">← 我的文档</Link><div className="document-title"><input aria-label="文档标题" value={title} maxLength={200} onChange={e=>setTitle(e.target.value)}/>{title!==doc.title&&<button disabled={action.isPending||!title.trim()} onClick={()=>perform(()=>unwrap(client.PATCH('/api/v1/documents/{document_id}',{params:docPath(doc.id),body:{title}})))}>保存标题</button>}</div><span className="version-pill">正文版本 {doc.current_version.number}</span></div>
    {action.error&&<p className="error" role="alert">{message(action.error)}</p>}{notice&&<p className="notice" role="status">{notice}</p>}
    {dirty&&base!==doc.current_version_id&&<p className="error">其他操作已更新正文。你的草稿仍保留，请复制草稿或重新打开文档后处理冲突。</p>}
    <div className="editor-grid"><section className="panel manuscript"><div className="panel-heading"><h2>正文</h2><span className="muted">{dirty?'有未保存修改':'已保存'}</span></div>
      <textarea className="body-editor" aria-label="正文" value={draft} maxLength={20000} onChange={e=>setDraft(e.target.value)} spellCheck={false}/>
      <div className="editor-bottom"><span>{draft.length.toLocaleString()} / 20,000 字符</span><button className="primary" disabled={!dirty||!draft.trim()||action.isPending} onClick={()=>perform(async()=>{const result=await unwrap(client.POST('/api/v1/documents/{document_id}/versions',{params:docPath(doc.id),body:{content:draft,base_version_id:base}}));setSaved(result.current_version.content);setBase(result.current_version_id);})}>保存正文</button></div>
      <div className="export-links"><a href={`/api/v1/documents/${doc.id}/export?format=md`} download>导出 Markdown ↗</a><a href={`/api/v1/documents/${doc.id}/export?format=txt`} download>导出纯文本 ↗</a></div>
    </section><section className="panel assistant"><div className="panel-heading"><h2>修改助手</h2><span className="muted">由你决定最终版本</span></div>
      <label>这次想怎样修改？<textarea aria-label="修改要求" rows={3} value={instruction} onChange={e=>setInstruction(e.target.value)} maxLength={2000}/></label>
      <button className="primary full" disabled={dirty||action.isPending||!instruction.trim()||tasks.data?.some(t=>['queued','running'].includes(t.status))} onClick={submitTask}>生成修改建议</button>
      {dirty&&<p className="hint">先保存正文，再基于保存的版本生成建议。</p>}
      {active?<div className="candidate-area"><div className="task-meta"><span className={`status ${active.status}`}>{labels[active.status]}</span><span>{stamp(active.created_at)}</span></div>
        {['queued','running'].includes(active.status)&&<div className="progress"><span className="spinner"/>任务在后台执行，刷新页面后仍可查看。<button className="quiet" disabled={action.isPending} onClick={()=>perform(()=>unwrap(client.POST('/api/v1/tasks/{task_id}/cancel',{params:taskPath(taskId)})))}>取消任务</button></div>}
        {active.error_message&&<p className="error">{active.error_message}</p>}
        {active.candidate&&<><p className="candidate-instruction">本轮要求：{active.instruction}</p><h3>修改对照</h3><Diff source={active.source_text} candidate={active.candidate.text}/><details><summary>查看完整候选</summary><pre>{active.candidate.text}</pre></details><p className="reason">{active.candidate.reason}</p></>}
        {active.status==='review'&&<><div className="actions"><button className="primary" disabled={dirty||action.isPending} onClick={()=>perform(()=>unwrap(client.POST('/api/v1/tasks/{task_id}/accept',{params:taskPath(taskId)})))}>接受并更新正文</button><button disabled={action.isPending} onClick={()=>perform(()=>unwrap(client.POST('/api/v1/tasks/{task_id}/cancel',{params:taskPath(taskId)})))}>放弃候选</button></div><label>哪里还需要改？<textarea aria-label="拒绝原因" rows={2} value={feedback} onChange={e=>setFeedback(e.target.value)} maxLength={2000} placeholder="写下拒绝原因，让下一次修改更准确"/></label><button disabled={!feedback.trim()||action.isPending} onClick={()=>perform(()=>unwrap(client.POST('/api/v1/tasks/{task_id}/reject',{params:taskPath(taskId),body:{reason:feedback}})))}>拒绝并保存原因</button></>}
        {active.status==='rejected'&&<><p className="feedback">你的反馈：{active.rejection_reason}</p><button className="primary" disabled={action.isPending||dirty} onClick={retry}>根据反馈重新生成</button></>}
        {active.status==='failed'&&<button disabled={action.isPending||dirty} onClick={retry}>重新尝试</button>}
        {!!active.runs.length&&<details className="run-details"><summary>运行记录 · {active.runs.length} 次尝试</summary>{active.runs.map(run=><div key={run.number}><strong>第 {run.number} 次 · {run.model}</strong><p>结果：{run.outcome} · 耗时：{run.duration_ms??'未知'} 毫秒</p><p>提示词版本：{run.prompt_version}</p><pre>{run.usage?JSON.stringify(run.usage,null,2):'演示模式或供应商未提供用量'}</pre></div>)}</details>}
      </div>:<div className="assistant-empty"><span>✦</span><p>你的原稿是起点。<br/>建议产生后，可在这里比较并选择。</p></div>}
    </section></div>
    <div className="history-grid"><section className="panel"><div className="panel-heading"><h2>正文历史</h2><span className="muted">恢复会创建新版本</span></div>{versions.data?.items.map(version=><details className="history-row" key={version.id}><summary><strong>版本 {version.number}</strong><span>{kinds[version.kind]??version.kind} · {stamp(version.created_at)}</span></summary><pre>{version.content}</pre>{version.id!==doc.current_version_id&&<button disabled={dirty||action.isPending} onClick={()=>restore(version.id)}>将此内容恢复为新版本</button>}</details>)}<div className="pagination"><button disabled={historyPage===1} onClick={()=>setHistoryPage(historyPage-1)}>上一页</button><span>{historyPage}</span><button disabled={!versions.data||historyPage*30>=versions.data.total} onClick={()=>setHistoryPage(historyPage+1)}>下一页</button></div></section>
    <section className="panel"><div className="panel-heading"><h2>修订任务</h2><span className="muted">候选与决定都被保留</span></div>{tasks.data?.map(item=><button className={`task-row ${taskId===item.id?'selected':''}`} key={item.id} onClick={()=>setSelected(item.id)}><span>{item.instruction.slice(0,60)}</span><span className={`status ${item.status}`}>{labels[item.status]}</span></button>)}{!tasks.data?.length&&<p className="muted">还没有修订任务。</p>}<div className="pagination"><button disabled={taskPage===1} onClick={()=>setTaskPage(taskPage-1)}>上一页</button><span>{taskPage}</span><button disabled={(tasks.data?.length??0)<30} onClick={()=>setTaskPage(taskPage+1)}>下一页</button></div></section></div>
    {(versions.error||tasks.error||task.error)&&<p className="error" role="alert">{message(versions.error||tasks.error||task.error)}</p>}
  </main>;
}

function Diff({source,candidate}:{source:string;candidate:string}) {
  const changes=diffChars(source,candidate,{timeout:50,maxEditLength:4000});
  return <div className="diff"><div className="diff-legend"><span><del>删除</del></span><span><ins>新增</ins></span></div><div className="diff-body">{changes?changes.map((part,index)=>part.added?<ins key={index}>{part.value}</ins>:part.removed?<del key={index}>{part.value}</del>:<span key={index}>{part.value}</span>):<><p className="muted">修改较多，直接比较完整内容：</p><pre>{source}</pre><hr/><pre>{candidate}</pre></>}</div></div>;
}
