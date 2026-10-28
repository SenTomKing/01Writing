import {useQuery} from '@tanstack/react-query';
import {client, unwrap, type Task} from './api';

interface Statistics {
  tasks:number; days:number; reported_tokens:number; returned_calls:number; unconfirmed_calls:number;
  retries:number; acceptance_rate:number|null; acceptance_denominator:number; median_elapsed_ms:number|null;
  p95_elapsed_ms:number|null; note:string; truncated:boolean;
}
const seconds=(value:unknown)=>typeof value==='number'?`${(value/1000).toFixed(2)} 秒`:'未记录';
export function AccountStatistics(){
  const stats=useQuery({queryKey:['statistics'],queryFn:async()=>await unwrap(client.GET('/api/v1/statistics')) as Statistics});
  const s=stats.data;
  return <details className="panel"><summary>我的运行统计 · 最近 30 天</summary>
    {stats.error&&<p role="alert">统计暂时不可用，请稍后重试。</p>}
    {s?<><p>{s.tasks} 个任务 · {s.returned_calls} 次已返回调用 · {s.reported_tokens.toLocaleString()} 已报告 Token · {s.retries} 次重试</p>
      <p>接受率：{s.acceptance_rate===null?'暂无决定':`${(s.acceptance_rate*100).toFixed(1)}%`}（分母 {s.acceptance_denominator}）</p>
      <p>端到端中位数 {seconds(s.median_elapsed_ms)} · P95 {seconds(s.p95_elapsed_ms)} · 未确认响应调用 {s.unconfirmed_calls}</p>
      <p>费用金额：未配置计价；{s.note}</p>{s.truncated&&<p>记录已截断到最近 9000 条。</p>}</>:<p>正在读取统计…</p>}
    <button onClick={()=>stats.refetch()}>刷新统计</button></details>;
}
export function TaskTelemetry({task}:{task:Task}){
  const s=task.telemetry??{};
  return <details><summary>任务耗时与用量</summary>
    <p>任务编号：{task.id}</p><p>请求编号：{String(s.request_id??'旧任务未记录')}</p>
    <p>首次排队 {seconds(s.queue_ms)} · 生成执行 {seconds(s.execution_ms)} · 端到端 {seconds(s.elapsed_ms)}</p>
    <p>已返回模型调用 {seconds(s.model_ms)} · Agent 检索工具 {seconds(s.retrieval_ms)} · 保存至数据库flush {seconds(s.save_flush_ms)}</p>
    <p>{String(s.returned_calls??0)} 次已返回调用 · {String(s.reported_tokens??0)} 已报告 Token · {String(s.unmetered_calls??0)} 次无计量记录 · {String(s.unconfirmed_calls??0)} 次响应未确认</p>
    <p>{String(s.retries??0)} 次重试 · {task.error_code?`错误代码：${task.error_code}`:'暂无错误'}</p>
    <p className="muted">{String(s.note??'旧任务耗时可能不完整')}</p></details>;
}
export function EvidenceRequirements({assessment}:{assessment:Record<string,unknown>}){
  const requirements=Array.isArray(assessment.requirements)?assessment.requirements:[];
  const missing=Array.isArray(assessment.missing)?assessment.missing:[];
  if(!requirements.length&&!missing.length)return null;
  return <details open><summary>逐项证据与缺口</summary><ul>{requirements.map((r,i)=><li key={i}>
    <strong>{String(r.requirement??'待核对事项')}</strong>
    {Array.isArray(r.evidence)&&r.evidence.map((e:Record<string,unknown>,j:number)=><blockquote key={j}>[{String(e.ref)}] {String(e.quote)}</blockquote>)}
  </li>)}</ul>{missing.map((m,i)=><p key={i}>待补充：{String(m)}</p>)}</details>;
}
