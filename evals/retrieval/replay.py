"""只读取本地缓存，重新排序并核对逐题指标；不创建任何网络客户端。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'backend/src'))
sys.path.insert(0,str(ROOT))
from evals.retrieval.core import SPLITTERS, rank, score, scoped_pieces, split_documents, validate_dataset
from evals.retrieval.run import MODEL_VARIANTS, digest


def replay(report, cache, data):
    validate_dataset(data)
    chunks = {name:split_documents(data['documents'],cfg) for name,cfg in SPLITTERS.items()}
    queries = {q['id']:q for q in data['queries']}
    def vector(model,text):
        return cache[hashlib.sha256((model+'\0'+text).encode()).hexdigest()]
    checked = 0
    for row in report['rows']:
        splitter,variant,method = row['config'].split('/')
        q = queries[row['id']]
        pieces = scoped_pieces(chunks[splitter],q['source_ids'])
        semantic = None
        if variant != 'none':
            model,prefix = MODEL_VARIANTS[variant]
            query_vector = vector(model,prefix+q['query'])
            semantic = [sum(a*b for a,b in zip(query_vector,vector(model,p['text']),strict=True)) for p in pieces]
        order = rank(pieces,q['query'],method,semantic)
        ranked = [pieces[i] for i in order]
        metrics = score(q['gold'],ranked)
        top = [{k:p[k] for k in ('doc_id','ref','start','end')} for p in ranked[:5]]
        if metrics != row['metrics'] or top != row['top5']:
            raise ValueError(f"复算不一致：{row['config']} / {row['id']}")
        checked += 1
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,default=ROOT/'evals/results/retrieval-v1.json')
    parser.add_argument('--cache',type=Path,default=ROOT/'private/retrieval-eval-v1/vectors.json')
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding='utf-8'))
    data_file = Path(__file__).with_name('dataset.json')
    for key,path in [('dataset_sha256',data_file),('core_sha256',Path(__file__).with_name('core.py')),
                     ('runner_sha256',Path(__file__).with_name('run.py'))]:
        if digest(path)!=report['protocol'][key]:
            raise ValueError('冻结文件哈希不匹配：'+key)
    count = replay(report,json.loads(args.cache.read_text(encoding='utf-8')),json.loads(data_file.read_text(encoding='utf-8')))
    print(f'离线复算通过：{count} 条逐题排名与指标完全一致；新增 API 调用 0 次。')


if __name__=='__main__':
    main()
