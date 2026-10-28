"""运行受预算保护的检索对照；只使用公开评测集，不读取业务数据库。"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend/src'))
sys.path.insert(0, str(ROOT))
from evals.retrieval.core import SPLITTERS, rank, score, scoped_pieces, split_documents, validate_dataset

MODEL_VARIANTS = {
    'bge-m3': ('BAAI/bge-m3', ''),
    'qwen3-plain': ('Qwen/Qwen3-Embedding-0.6B', ''),
    'qwen3-instruct': ('Qwen/Qwen3-Embedding-0.6B', 'Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery:'),
}
BASELINE = 'boundary900_120/bge-m3/rrf_keyword'
MAX_CALLS = 96
MAX_INPUT_BYTES = 900_000


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Embeddings:
    def __init__(self, directory, env_file):
        from dotenv import dotenv_values
        from openai import OpenAI
        values = dotenv_values(env_file)
        url = (values.get('EMBEDDING_BASE_URL') or '').rstrip('/')
        if url != 'https://api.siliconflow.cn/v1' or not values.get('EMBEDDING_API_KEY'):
            raise ValueError('本实验需要本机配置的硅基流动地址和密钥；不会将密钥转发到其他服务')
        self.client = OpenAI(api_key=values['EMBEDDING_API_KEY'], base_url=url, timeout=30, max_retries=0)
        self.directory = directory
        self.cache_file = directory/'vectors.json'
        self.ledger_file = directory/'ledger.json'
        self.cache = json.loads(self.cache_file.read_text(encoding='utf-8')) if self.cache_file.exists() else {}
        self.ledger = json.loads(self.ledger_file.read_text(encoding='utf-8')) if self.ledger_file.exists() else []

    def get(self, model, texts, purpose, batch_size=16):
        keys = [hashlib.sha256((model+'\0'+t).encode()).hexdigest() for t in texts]
        missing = dict((k,t) for k,t in zip(keys,texts) if k not in self.cache)
        items = list(missing.items())
        for offset in range(0,len(items),batch_size):
            batch = items[offset:offset+batch_size]
            size = sum(len(t.encode()) for _,t in batch)
            if len(self.ledger) >= MAX_CALLS or sum(r['input_bytes'] for r in self.ledger)+size > MAX_INPUT_BYTES:
                raise RuntimeError('达到预先冻结的实验调用预算，停止，不自动追加')
            record = {'model':model, 'purpose':purpose, 'count':len(batch), 'input_bytes':size,
                'status':'started', 'at':datetime.now(timezone.utc).isoformat()}
            self.ledger.append(record)
            write_json(self.ledger_file,self.ledger)
            started = time.perf_counter()
            try:
                response = self.client.embeddings.create(model=model, input=[t for _,t in batch], encoding_format='float')
                record['usage'] = response.usage.model_dump() if response.usage else None
                ordered = sorted(response.data,key=lambda r:r.index)
                if [r.index for r in ordered] != list(range(len(batch))):
                    raise ValueError('返回的向量条数或序号错误')
                dimension = len(ordered[0].embedding)
                for (key,_),row in zip(batch,ordered,strict=True):
                    vector = row.embedding
                    if not 1 <= dimension <= 4096 or len(vector) != dimension or not all(math.isfinite(v) for v in vector):
                        raise ValueError('向量格式不正确')
                    norm = math.sqrt(sum(v*v for v in vector))
                    if norm == 0:
                        raise ValueError('向量为零')
                    self.cache[key] = [v/norm for v in vector]
                record.update(status='ok', dimension=dimension)
                write_json(self.cache_file,self.cache)
            except Exception as exc:
                record.update(status='error', error_type=type(exc).__name__, http_status=getattr(exc,'status_code',None))
                raise RuntimeError(f"{model} 调用失败：{type(exc).__name__}；原始响应不输出") from None
            finally:
                record['elapsed_ms'] = round((time.perf_counter()-started)*1000,3)
                write_json(self.ledger_file,self.ledger)
            print(f"API {len(self.ledger)}/{MAX_CALLS} {model} {purpose}: {len(batch)}条",flush=True)
        return [self.cache[k] for k in keys]


def percentile(values, p):
    if not values:
        return None
    values = sorted(values)
    pos = (len(values)-1)*p
    lo,hi = math.floor(pos),math.ceil(pos)
    return values[lo]+(values[hi]-values[lo])*(pos-lo)


def aggregate(rows, split):
    answered = [r for r in rows if r['split']==split and r['answerable']]
    negatives = [r for r in rows if r['split']==split and not r['answerable']]
    result = {'n_answerable':len(answered),'n_unanswerable':len(negatives)}
    if answered:
        for key in answered[0]['metrics']:
            result[key] = statistics.mean(r['metrics'][key] for r in answered)
        result['rank_ms_p50'] = percentile([r['rank_ms'] for r in answered],.5)
        result['rank_ms_p95'] = percentile([r['rank_ms'] for r in answered],.95)
    result['unanswerable_return_rate'] = statistics.mean(r['metrics']['no_answer_returned'] for r in negatives) if negatives else None
    return result


def cluster_delta(rows, selected, baseline, metric):
    scores = defaultdict(dict)
    for row in rows:
        if row['split']=='test' and row['answerable'] and row['config'] in {selected,baseline}:
            scores[row['config']][row['id']] = row
    families = defaultdict(list)
    for q,row in scores[selected].items():
        families[row['family']].append(row['metrics'][metric]-scores[baseline][q]['metrics'][metric])
    means = [statistics.mean(v) for v in families.values()]
    rng = random.Random(20261027)
    samples = [statistics.mean(rng.choices(means,k=len(means))) for _ in range(2000)]
    return {'metric':metric,'delta':statistics.mean(means),'ci95':[percentile(samples,.025),percentile(samples,.975)],
        'family_count':len(means),'method':'按资料家族成组重采样，2000次；家族很少，不作为可靠显著性证明'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--env-file', type=Path, default=ROOT/'.env.deploy')
    parser.add_argument('--cache', type=Path, default=ROOT/'private/retrieval-eval-v1')
    parser.add_argument('--output', type=Path, default=ROOT/'evals/results/retrieval-v1.json')
    args = parser.parse_args()
    data_file = Path(__file__).with_name('dataset.json')
    data = json.loads(data_file.read_text(encoding='utf-8'))
    validate_dataset(data)
    chunks = {name:split_documents(data['documents'],cfg) for name,cfg in SPLITTERS.items()}
    protocol = {'dataset_sha256':digest(data_file), 'runner_sha256':digest(Path(__file__)),
        'core_sha256':digest(Path(__file__).with_name('core.py')), 'splitters':SPLITTERS,
        'models':MODEL_VARIANTS, 'max_calls':MAX_CALLS, 'max_input_bytes':MAX_INPUT_BYTES,
        'selection':'仅开发集：1500字符预算证据召回率降序→证据MRR@5降序→上下文字符数升序→配置名',
        'baseline':BASELINE,'python':platform.python_version(),
        'at':datetime.now(timezone.utc).isoformat()}
    frozen = args.cache/'protocol.json'
    if frozen.exists():
        old = json.loads(frozen.read_text(encoding='utf-8'))
        for key in ('dataset_sha256','runner_sha256','core_sha256','selection'):
            if old[key] != protocol[key]:
                raise ValueError('实验材料或代码发生变化，请用新的实验编号；不得覆盖已冻结结果')
        protocol = old
    else:
        write_json(frozen,protocol)
    print(json.dumps({'queries':len(data['queries']),'source_documents':len(data['documents']),
        'chunks':{k:sum(len(p) for p in v.values()) for k,v in chunks.items()},'live':args.live},ensure_ascii=False),flush=True)
    if not args.live:
        print('方案与数据已检查并冻结；未调用服务。使用 --live 执行真实实验。')
        return
    api = Embeddings(args.cache,args.env_file)
    by_model, query_vectors, failures = {}, {}, {}
    sample_queries = [q for q in data['queries'] if q['id'].endswith('-q1')]
    for variant,(model,prefix) in MODEL_VARIANTS.items():
        try:
            if model not in by_model:
                by_model[model] = {}
                for name,by_doc in chunks.items():
                    flat = [p for d in data['documents'] for p in by_doc[d['id']]]
                    vectors = api.get(model,[p['text'] for p in flat], 'document:'+name)
                    by_model[model][name] = {(p['doc_id'],p['start']):v for p,v in zip(flat,vectors,strict=True)}
            # 八个不同家族的问题逐个请求，单独测网络服务延迟；其余批量节省请求。
            for q in sample_queries:
                api.get(model,[prefix+q['query']], 'single_query:'+variant,batch_size=1)
            query_vectors[variant] = dict(zip([q['id'] for q in data['queries']],
                api.get(model,[prefix+q['query'] for q in data['queries']], 'query_batch:'+variant),strict=True))
        except RuntimeError as exc:
            failures[variant] = str(exc)
            print(str(exc),flush=True)
    rows = []
    configs = []
    for splitter,by_doc in chunks.items():
        configs.extend((splitter,'none',method) for method in ('keyword','bm25'))
        for variant in query_vectors:
            configs.extend((splitter,variant,method) for method in ('dense','rrf_keyword','rrf_bm25'))
    for splitter,variant,method in configs:
        name = f'{splitter}/{variant}/{method}'
        for q in data['queries']:
            pieces = scoped_pieces(chunks[splitter],q['source_ids'])
            started = time.perf_counter()
            semantic = None
            if variant != 'none':
                model = MODEL_VARIANTS[variant][0]
                vector = query_vectors[variant][q['id']]
                bank = by_model[model][splitter]
                semantic = [sum(a*b for a,b in zip(vector,bank[p['doc_id'],p['start']],strict=True)) for p in pieces]
            order = rank(pieces,q['query'],method,semantic)
            elapsed = (time.perf_counter()-started)*1000
            ranked = [pieces[i] for i in order]
            rows.append({'config':name,'id':q['id'],'family':q['family'],'split':q['split'], 'category':q['category'],
                'answerable':bool(q['gold']),'metrics':score(q['gold'],ranked),'rank_ms':elapsed,
                'top5':[{k:p[k] for k in ('doc_id','ref','start','end')} for p in ranked[:5]]})
        print('完成评分 '+name,flush=True)
    groups = defaultdict(list)
    for row in rows:
        groups[row['config']].append(row)
    summaries = {key:{s:aggregate(values,s) for s in ('dev','test')} for key,values in groups.items()}
    eligible = [key for key in summaries]
    selected = sorted(eligible,key=lambda key:(-summaries[key]['dev']['evidence_recall_budget1500'],
        -summaries[key]['dev']['mrr@5'],summaries[key]['dev']['context_chars'],key))[0]
    results = {'protocol':protocol, 'completed':len(query_vectors)==len(MODEL_VARIANTS), 'failures':failures,
        'selected_on_dev':selected,'summaries':summaries,'rows':rows,
        'paired_test_delta':cluster_delta(rows,selected,BASELINE,'evidence_recall_budget1500') if BASELINE in summaries else None,
        'api_requests':api.ledger,'actual_api_calls':len(api.ledger),
        'actual_total_tokens':sum((r.get('usage') or {}).get('total_tokens',0) for r in api.ledger),
        'rmb_cost':None, 'cost_note':'记录供应商实际tokens；未取得本账户结算单或冻结单价，人民币费用不估填。',
        'at':datetime.now(timezone.utc).isoformat()}
    write_json(args.output,results)
    print(json.dumps({'selected_on_dev':selected,'configs':len(configs),'trials':len(rows),
        'calls':results['actual_api_calls'],'tokens':results['actual_total_tokens'], 'completed':results['completed']},ensure_ascii=False))


if __name__ == '__main__':
    main()
