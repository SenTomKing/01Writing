"""v4受控检索实验。默认仅校验，--live收费；--replay禁止网络请求。"""
from pathlib import Path
import argparse, hashlib, json, sys, time, statistics
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'backend/src'))
from evals.retrieval import run as old_run
from evals.retrieval.core import split_documents,scoped_pieces,rank,covered,validate_dataset,budget_context
from writing_server.evidence_context import assemble

CONFIGS={'boundary900_120':{'mode':'boundary','size':900,'overlap':120},
         'boundary300_60':{'mode':'boundary','size':300,'overlap':60}}
MODELS={'bge-m3':'BAAI/bge-m3','qwen3':'Qwen/Qwen3-Embedding-0.6B'}
BUDGET=1800

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--replay',action='store_true');args=parser.parse_args()
    folder=Path(__file__).parent;private=ROOT/'private/v4-eval';data_path=folder/'dataset.json'
    data=json.loads(data_path.read_text(encoding='utf-8'));validate_dataset(data)
    protocol={'dataset_sha256':old_run.digest(data_path),'runner_sha256':old_run.digest(Path(__file__)),
        'assembly_sha256':old_run.digest(ROOT/'backend/src/writing_server/evidence_context.py'),
        'splitters':CONFIGS,'models':MODELS,'body_budget':BUDGET,'max_embedding_calls':64,'max_embedding_input_bytes':900000,
        'comparison':'同一候选排序，旧版前5片段按1800字符截断；新版前12候选装配最多6窗口。未选择测试集最优参数。',
        'generation_cases':['stone-condition','sail-missing','moon-conflict','pine-fact']}
    frozen=private/'protocol.json'
    if frozen.exists():
        assert json.loads(frozen.read_text(encoding='utf-8'))==protocol,'实验协议已变化，需新编号'
    else:old_run.write_json(frozen,protocol)
    old_run.write_json(folder/'protocol.json',protocol)
    if not args.live and not args.replay:
        print('方案已冻结，未调用模型');return
    old_run.MAX_CALLS=64;old_run.MAX_INPUT_BYTES=900000
    api=old_run.Embeddings(private,ROOT/'.env.deploy')
    if args.replay:
        def no_network(*a,**kw):raise RuntimeError('重放缺少缓存，禁止自动请求')
        api.client.embeddings.create=no_network
    rows=[]
    for splitter,config in CONFIGS.items():
        by_doc=split_documents(data['documents'],config)
        flat=[p for d in data['documents'] for p in by_doc[d['id']]]
        vectors={};queries={}
        for alias,model in MODELS.items():
            vectors[alias]={(p['doc_id'],p['start']):v for p,v in zip(flat,api.get(model,[p['text'] for p in flat],splitter),strict=True)}
            queries[alias]=dict(zip([q['id'] for q in data['queries']],api.get(model,[q['query'] for q in data['queries']],'queries'),strict=True))
        for alias,method in [('none','keyword')]+[(a,m) for a in MODELS for m in ('dense','rrf_keyword')]:
            for q in data['queries']:
                pieces=scoped_pieces(by_doc,q['source_ids']);semantic=None
                if alias!='none':semantic=[sum(a*b for a,b in zip(queries[alias][q['id']],vectors[alias][(p['doc_id'],p['start'])],strict=True)) for p in pieces]
                ordered=[pieces[i] for i in rank(pieces,q['query'],method,semantic)]
                for packing in ('prefix','windows'):
                    started=time.perf_counter()
                    if packing=='prefix':selected=budget_context(ordered[:5],BUDGET)
                    else:
                        candidates=[dict(p,offset=p['start']) for p in ordered[:12]]
                        selected=[dict(p,start=p['offset'],end=p['offset']+len(p['text'])) for p in assemble(candidates,q['query'],BUDGET)]
                    recall=sum(covered(g,selected) for g in q['gold'])/len(q['gold']) if q['gold'] else None
                    rows.append({'id':q['id'],'split':q['split'],'expected':q['expected'],
                        'config':f'{splitter}/{alias}/{method}/{packing}',
                        'recall':recall,'all_evidence':recall==1 if recall is not None else None,
                        'chars':sum(p['end']-p['start'] for p in selected),'returned':len(selected),
                        'packing_ms':round((time.perf_counter()-started)*1000,3),
                        'selected':[{'ref':p['ref'],'doc_id':p['doc_id'],'start':p['start'],'end':p['end']} for p in selected]})
    summary=[]
    for config in sorted({x['config'] for x in rows}):
        for split in ('dev','test'):
            group=[x for x in rows if x['config']==config and x['split']==split];positive=[x for x in group if x['recall'] is not None]
            summary.append({'config':config,'split':split,'n':len(positive),'recall':statistics.mean(x['recall'] for x in positive),
                'complete':sum(x['all_evidence'] for x in positive),'chars':round(statistics.mean(x['chars'] for x in group),1),
                'missing_returned':sum(x['returned']>0 for x in group if x['expected']=='clarify')})
    output={'protocol':protocol,'rows':rows,'summary':summary,'embedding_calls':len(api.ledger),
        'embedding_tokens':sum((x.get('usage') or {}).get('total_tokens',0) for x in api.ledger),
        'ledger':api.ledger,'limits':'合成、同模板、每题一至两份资料；结果不外推到真实论文或大规模语料。检索命中不能证明语义支持。'}
    path=ROOT/'evals/results/v4-context.json'
    if args.replay:
        existing=json.loads(path.read_text(encoding='utf-8'));assert [{k:v for k,v in x.items() if k!='packing_ms'} for x in existing['rows']]==[{k:v for k,v in x.items() if k!='packing_ms'} for x in rows]
        print('全部',len(rows),'条确定性检索结果重放一致，无新增API调用')
    else:old_run.write_json(path,output)
    print(json.dumps({'calls':len(api.ledger),'tokens':output['embedding_tokens'],'test':[x for x in summary if x['split']=='test' and x['config'].startswith('boundary900_120/bge-m3/rrf_keyword')]},ensure_ascii=False))
    api.client.close()

if __name__=='__main__':main()
