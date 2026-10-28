"""隔离测试库的临时表基准；不接触业务表，不产生模型费用。"""
from pathlib import Path
import json,random,math,time,statistics,sys
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/'backend/src'))
from sqlalchemy import create_engine,text
from writing_server.settings import Settings

def main():
    settings=Settings.load()
    # 防止误在正式部署运行；本轮只允许专用隔离容器端口。
    from sqlalchemy.engine import make_url
    url=make_url(settings.migration_url) if isinstance(settings.migration_url,str) else settings.migration_url
    if url.host!='127.0.0.1' or url.port!=55434:raise SystemExit('仅允许本机55434隔离基准库')
    rng=random.Random(20261028);dimension=1024;n=1500;query_count=24
    def vector():
        v=[rng.gauss(0,1) for _ in range(dimension)];norm=math.sqrt(sum(x*x for x in v));return json.dumps([x/norm for x in v])
    rows=[{'id':i,'v':vector()} for i in range(n)];queries=[vector() for _ in range(query_count)]
    engine=create_engine(settings.migration_url,hide_parameters=True)
    sql=text('SELECT id FROM ann_v4 ORDER BY embedding OPERATOR(public.<=>) CAST(:q AS public.vector) LIMIT 10')
    result={'seed':20261028,'rows':n,'dimensions':dimension,'queries':query_count,'kind':'独立高斯合成向量，非语义检索数据', 'variants':[]}
    with engine.connect() as conn:
        conn.execute(text('CREATE TEMP TABLE ann_v4 (id integer PRIMARY KEY, embedding public.vector(1024))'))
        conn.execute(text('INSERT INTO ann_v4 VALUES (:id,CAST(:v AS public.vector))'),rows)
        conn.execute(text('ANALYZE ann_v4'))
        gold=[];times=[]
        for q in queries:
            started=time.perf_counter();gold.append(list(conn.scalars(sql,{'q':q})));times.append((time.perf_counter()-started)*1000)
        result['variants'].append({'method':'exact','recall_at_10':1.,'mean_ms':statistics.mean(times),'p95_ms':sorted(times)[22]})
        started=time.perf_counter()
        conn.execute(text('CREATE INDEX ann_v4_hnsw ON ann_v4 USING hnsw (embedding public.vector_cosine_ops) WITH (m=16,ef_construction=64)'))
        result['build_ms']=(time.perf_counter()-started)*1000
        result['index_bytes']=conn.scalar(text("SELECT pg_relation_size('ann_v4_hnsw')"))
        conn.execute(text('SET LOCAL enable_seqscan=off'))
        for ef in (40,100):
            conn.execute(text(f'SET LOCAL hnsw.ef_search={ef}'));scores=[];times=[]
            plan=list(conn.scalars(text('EXPLAIN SELECT id FROM ann_v4 ORDER BY embedding OPERATOR(public.<=>) CAST(:q AS public.vector) LIMIT 10'),{'q':queries[0]}))
            assert any('ann_v4_hnsw' in x for x in plan),plan
            for q,expected in zip(queries,gold,strict=True):
                started=time.perf_counter();ids=list(conn.scalars(sql,{'q':q}));times.append((time.perf_counter()-started)*1000)
                scores.append(len(set(ids)&set(expected))/10)
            result['variants'].append({'method':f'hnsw_ef{ef}','recall_at_10':statistics.mean(scores),
                'mean_ms':statistics.mean(times),'p95_ms':sorted(times)[22],'plan':plan[:2],'per_query_recall':scores})
        # 临时表随连接消失，事务回滚；不清理任何业务表。
        conn.rollback()
    engine.dispose()
    path=ROOT/'evals/results/v4-ann.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='variants'}));print([{k:v for k,v in x.items() if k not in {'plan','per_query_recall'}} for x in result['variants']])

if __name__=='__main__':main()
