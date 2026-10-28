import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from evals.retrieval.core import (SPLITTERS, budget_context, covered, rank, score, split_documents,
                                 scoped_pieces, validate_dataset)
from writing_server.retrieval import retrieve, embedding_id
from writing_server.settings import Settings


def piece(start,end,doc='a'):
    return {'doc_id':doc,'start':start,'end':end,'text':'x'*(end-start),'title':'资料','ref':f'{doc}:{start}'}


def test_span_recall_can_combine_adjacent_chunks_but_not_gap():
    gold = {'doc_id':'a','start':8,'end':14}
    assert covered(gold,[piece(0,10),piece(10,20)])
    assert not covered(gold,[piece(0,10),piece(11,20)])
    assert not covered(gold,[piece(0,20,'b')])


def test_metrics_known_values_and_empty_rank():
    gold = [{'doc_id':'a','start':8,'end':14},{'doc_id':'b','start':0,'end':3}]
    result = score(gold,[piece(0,20),piece(0,20),piece(0,20,'irrelevant')])
    assert result['evidence_recall@5'] == .5
    assert result['hit@5'] == 1 and result['all_evidence@5'] == 0
    assert result['mrr@5'] == 1 and result['precision@5'] == 2/5
    assert result['redundancy'] == pytest.approx(1/3)
    assert score(gold,[])['evidence_recall@5'] == 0
    assert score(gold,[piece(0,20,'x'),piece(0,20)])['mrr@5'] == .5


def test_no_answer_not_silently_counted_as_recall_success():
    result = score([], [piece(0,10)])
    assert result['no_answer_returned']==1 and 'evidence_recall@5' not in result
    assert score([],[])['no_answer_returned']==0


def test_context_budget_does_not_credit_truncated_gold():
    pieces = [piece(0,900,'a'),piece(0,900,'b')]
    selected = budget_context(pieces)
    assert selected[-1]['end'] == 600
    gold = [{'doc_id':'b','start':650,'end':690}]
    result=score(gold,pieces)
    assert result['evidence_recall@5']==1 and result['evidence_recall_budget1500']==0


def test_all_splitters_keep_entire_document_and_real_offsets():
    text = '主题说明。' * 300
    for cfg in SPLITTERS.values():
        pieces=split_documents([{'id':'a','title':'标题','content':text}],cfg)['a']
        assert covered({'doc_id':'a','start':0,'end':len(text)},pieces)
        assert all(p['text']==text[p['start']:p['end']] for p in pieces)


def test_keyword_and_bm25_relevance_and_empty_query():
    pieces=[dict(piece(0,3),text='索引查询',title='数据库'),dict(piece(3,6),text='种植番茄',title='农业')]
    assert rank(pieces,'数据库索引','keyword')==[0]
    assert rank(pieces,'数据库索引','bm25')==[0]
    assert rank(pieces,'','bm25')==[]


def test_dataset_annotations_and_family_holdout():
    data=json.loads((ROOT/'evals/retrieval/dataset.json').read_text(encoding='utf-8'))
    assert validate_dataset(data)
    assert len(data['queries'])==72
    assert sum(bool(q['gold']) for q in data['queries'])==64
    assert sum(q['split']=='test' and bool(q['gold']) for q in data['queries'])==32


def test_keyword_rrf_matches_production_top5(monkeypatch):
    import hashlib
    from writing_server import retrieval
    config=Settings(database_url='unused',migration_url=None,embedding_url='https://example.com/v1',embedding_model='test',embedding_key='fake')
    docs=[{'id':'a','title':'数据库','content':'查询很慢需要优化索引。'*100}, {'id':'b','title':'农业','content':'番茄需要充足光照。'*100}]
    by_doc=split_documents(docs,SPLITTERS['boundary900_120'])
    sources=[]
    for doc in docs:
        chunks=[{'offset':p['start'],'text':p['text'],'page':None,'vector':[1.,0.] if doc['id']=='a' else [0.,1.]} for p in by_doc[doc['id']]]
        sources.append(dict(doc,chunk_index={'hash':hashlib.sha256(doc['content'].encode()).hexdigest(),'embedding_id':embedding_id(config),'chunks':chunks}))
    monkeypatch.setattr(retrieval,'embed',lambda *a:[[1.,0.]])
    pieces=scoped_pieces(by_doc,['a','b'])
    scores=[1. if p['doc_id']=='a' else 0. for p in pieces]
    order=rank(pieces,'索引查询慢','rrf_keyword',scores)
    expected=retrieve(sources,'索引查询慢',config)
    assert [pieces[i]['ref'] for i in order[:5]]==[p['ref'] for p in expected['chunks']]


def runner():
    spec=importlib.util.spec_from_file_location('retrieval_runner',ROOT/'evals/retrieval/run.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_api_budget_prevents_network(tmp_path):
    mod=runner()
    obj=mod.Embeddings.__new__(mod.Embeddings)
    obj.cache={};obj.ledger=[{'input_bytes':0}]*mod.MAX_CALLS
    obj.cache_file=tmp_path/'vectors.json';obj.ledger_file=tmp_path/'ledger.json'
    class Client:
        @property
        def embeddings(self):pytest.fail('调用预算耗尽后不能请求供应商')
    obj.client=Client()
    with pytest.raises(RuntimeError,match='预算'):obj.get('fake',['text'],'test')


def test_aggregate_separates_negative_questions():
    mod=runner()
    rows=[{'split':'test','answerable':True,'rank_ms':1,'metrics':score([{'doc_id':'a','start':0,'end':2}],[piece(0,10)])},
          {'split':'test','answerable':False,'rank_ms':1,'metrics':score([],[piece(0,10)])}]
    summary=mod.aggregate(rows,'test')
    assert summary['n_answerable']==1 and summary['n_unanswerable']==1
    assert summary['evidence_recall@5']==1 and summary['unanswerable_return_rate']==1


def test_cache_duplicates_use_one_request_and_normalize_vectors(tmp_path):
    from types import SimpleNamespace
    mod=runner()
    obj=mod.Embeddings.__new__(mod.Embeddings)
    obj.cache={};obj.ledger=[]
    obj.cache_file=tmp_path/'vectors.json';obj.ledger_file=tmp_path/'ledger.json'
    calls=[]
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(index=0,embedding=[3.,4.])],usage=None)
    obj.client=SimpleNamespace(embeddings=SimpleNamespace(create=create))
    assert obj.get('fake',['same','same'],'test')==[[.6,.8],[.6,.8]]
    assert obj.get('fake',['same'],'test')==[[.6,.8]]
    assert len(calls)==1 and calls[0]['input']==['same']
    assert len(obj.ledger)==1 and obj.ledger[0]['status']=='ok'


def test_cached_scores_replay_without_any_api(tmp_path):
    from evals.retrieval.replay import replay
    data={'documents':[{'id':'a','title':'测试主题','content':'项目预期收集84份样本。'}],
          'queries':[{'id':'one','family':'a','split':'test','source_ids':['a'],'query':'样本',
                      'gold':[{'doc_id':'a','start':6,'end':8,'quote':'84'}]}]}
    pieces=scoped_pieces(split_documents(data['documents'],SPLITTERS['fixed300']),['a'])
    row={'config':'fixed300/none/keyword','id':'one','metrics':score(data['queries'][0]['gold'],pieces),
         'top5':[{k:p[k] for k in ('doc_id','ref','start','end')} for p in pieces]}
    assert replay({'rows':[row]}, {},data)==1
    row['metrics']['evidence_recall@5']=0
    with pytest.raises(ValueError,match='复算不一致'):replay({'rows':[row]}, {},data)
