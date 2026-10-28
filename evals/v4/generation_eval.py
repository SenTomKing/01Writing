"""4个预先指定任务的新旧Agent真实对照，最大48次聊天/核验调用，逐题落盘。"""
from pathlib import Path
import argparse,importlib.util,json,sys,time,hashlib
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'backend/src'))
from dotenv import dotenv_values
from openai import OpenAI
from evals.retrieval import run as embedding_run
from writing_server import agent,retrieval,evidence
from writing_server.settings import Settings
from writing_server.generation import ModelFailure

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');args=parser.parse_args()
    if not args.live:raise SystemExit('仅 --live 才会调用真实模型')
    directory=ROOT/'private/v4-eval';folder=Path(__file__).parent
    data=json.loads((folder/'dataset.json').read_text(encoding='utf-8'));protocol=json.loads((folder/'protocol.json').read_text(encoding='utf-8'))
    assert embedding_run.digest(folder/'dataset.json')==protocol['dataset_sha256']
    frozen={'dataset':protocol['dataset_sha256'],'runner':embedding_run.digest(Path(__file__)),
        'old_agent':embedding_run.digest(folder/'legacy_agent.py'),'new_agent':embedding_run.digest(ROOT/'backend/src/writing_server/agent.py'),
        'case_ids':protocol['generation_cases'],'max_calls':48,'model':'deepseek-flash','temperature':0.2}
    frozen_path=directory/'generation-protocol.json'
    if frozen_path.exists():assert json.loads(frozen_path.read_text(encoding='utf-8'))==frozen
    else:embedding_run.write_json(frozen_path,frozen)
    embedding_run.write_json(folder/'generation-protocol.json',frozen)
    values=dotenv_values(ROOT/'.env.deploy')
    settings=Settings(database_url='unused',migration_url=None,provider='deepseek',model=frozen['model'],
        api_key=values['DEEPSEEK_API_KEY'],embedding_key=values['EMBEDDING_API_KEY'],
        embedding_url=values['EMBEDDING_BASE_URL'],embedding_model=values['EMBEDDING_MODEL'])
    client=OpenAI(api_key=settings.api_key,base_url=settings.base_url,max_retries=0,timeout=30)
    embedding_run.MAX_CALLS=64;embedding_run.MAX_INPUT_BYTES=900000
    vectors=embedding_run.Embeddings(directory,ROOT/'.env.deploy')
    retrieval.embed=lambda s,texts:vectors.get(s.embedding_model,texts,'generation-query')
    sources={}
    for doc in data['documents']:
        source=dict(doc);source['chunk_index']=retrieval.build_index(settings,source['content']);sources[doc['id']]=source
    spec=importlib.util.spec_from_file_location('writing_server.eval_legacy_agent',folder/'legacy_agent.py')
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    output_path=ROOT/'evals/results/v4-generation.json'
    result=json.loads(output_path.read_text(encoding='utf-8')) if output_path.exists() else {'protocol':frozen,'rows':[],'calls':[]}
    def save():embedding_run.write_json(output_path,result)
    active={}
    def on_call():
        if len(result['calls'])>=48:raise RuntimeError('达到48次预留调用预算')
        result['calls'].append(dict(active,status='started'));save()
    def complete(messages,tools):
        started=time.monotonic()
        try:
            response=client.chat.completions.create(model=settings.model,messages=messages,tools=tools,
                tool_choice='required',temperature=.2,max_tokens=4096,extra_body={'thinking':{'type':'disabled'}})
            result['calls'][-1].update(status='ok',purpose='agent',usage=response.usage.model_dump(),elapsed_ms=round((time.monotonic()-started)*1000))
            return {'message':response.choices[0].message.model_dump(exclude_none=True),'model':response.model,'usage':response.usage.model_dump()}
        finally:save()
    original_audit=evidence.audit
    def audit(*args,**kwargs):
        started=time.monotonic();report,usage=original_audit(*args,**kwargs)
        result['calls'][-1].update(status='ok',purpose='audit',usage=usage,elapsed_ms=round((time.monotonic()-started)*1000));save()
        return report,usage
    evidence.audit=audit
    for case_id in frozen['case_ids']:
        q=next(q for q in data['queries'] if q['id']==case_id)
        for name,module in [('v3',old),('v4',agent)]:
            if any(row['id']==case_id and row['strategy']==name for row in result['rows']):continue
            active.update(id=case_id,strategy=name);started=time.monotonic();snapshots=[]
            context={'sources':[sources[i] for i in q['source_ids']],'memories':[],'allow_web':False}
            def ranker(selected,query):
                query_vector=vectors.get(settings.embedding_model,[query],'generation-query')[0]
                scored={f"S{i}:{p['offset']}":retrieval.cosine(query_vector,p['vector'])
                    for i,s in enumerate(selected,1) for p in s['chunk_index']['chunks']}
                return scored,'cached_exact','评测内使用缓存向量精确余弦，生产数据库链路另测'
            row={'id':case_id,'strategy':name,'expected':q['expected']}
            try:
                extra={'retriever':ranker} if name=='v4' else {}
                generated=module.run_agent(settings,source=q['source'],instruction=q['query'],feedback=[],provider='deepseek',
                    model=settings.model,context=context,completion=complete,on_call=on_call,
                    save=lambda state,message:snapshots.append(state),**extra)
                row.update(status='candidate',text=generated.candidate.text,reason=generated.candidate.reason,usage=generated.usage)
            except ModelFailure as exc:row.update(status=exc.code,message=str(exc),usage=exc.usage)
            except Exception as exc:row.update(status='harness_error',error_type=type(exc).__name__)
            row['elapsed_ms']=round((time.monotonic()-started)*1000)
            row['tool_outputs']=[m for m in (snapshots[-1]['messages'] if snapshots else []) if m['role']=='tool']
            result['rows'].append(row);save();print(case_id,name,row['status'],'calls',len(result['calls']),flush=True)
    result['embedding_calls_total']=len(vectors.ledger)
    result['embedding_tokens_total']=sum((r.get('usage') or {}).get('total_tokens',0) for r in vectors.ledger)
    result['chat_tokens']=sum((r.get('usage') or {}).get('total_tokens',0) for r in result['calls']);save()
    client.close();vectors.client.close()

if __name__=='__main__':main()
