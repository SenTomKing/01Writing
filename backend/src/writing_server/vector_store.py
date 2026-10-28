"""不可变资料索引；按用户和任务快照限定范围后执行 PostgreSQL 精确向量查询。"""
import hashlib
import json
import math

from sqlalchemy import select, text

from .tables import SourceIndex, SourceChunk, uid
from .retrieval import valid_index, embedding_id, embed


def persist_index(session, user_id, source):
    index = valid_index(source)
    pieces = index.get('chunks') or []
    if not pieces:
        return None
    # 标题、来源、内容和索引配置共同标识一份不可变索引，重建不能改变旧任务。
    fingerprint = hashlib.sha256(json.dumps({
        'source':source['id'],'title':source['title'],'index':index,
    },ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    existing = session.scalar(select(SourceIndex).where(SourceIndex.user_id==user_id, SourceIndex.fingerprint==fingerprint))
    if existing:
        return existing.id
    row=SourceIndex(id=uid(),user_id=user_id,source_id=source['id'],fingerprint=fingerprint,
        content_hash=index['hash'],title=source['title'],embedding_id=index.get('embedding_id',''),
        config={k:v for k,v in index.items() if k!='chunks'})
    session.add(row);session.flush()
    for piece in pieces:
        value=piece.get('vector')
        if value is not None and (not 1<=len(value)<=4096 or not all(math.isfinite(x) for x in value) or not any(value)):
            raise ValueError('不能保存非法向量')
        session.add(SourceChunk(index_id=row.id,user_id=user_id,offset=piece['offset'],
            content=piece['text'],page=piece.get('page'),embedding=value))
    session.flush()
    return row.id


def snapshot_index(session, user_id, source):
    """已有JSON索引懒迁移，不重新计费；快照保留原文位置，向量留在数据库。"""
    index_id=persist_index(session,user_id,source)
    if index_id:
        source['index_id']=index_id
        source['chunk_index']={**source['chunk_index'],'chunks':[
            {k:v for k,v in p.items() if k!='vector'} for p in source['chunk_index']['chunks']]}
    return source


def task_ranker(db, settings, task_id, user_id):
    from .tables import Task
    def rank(sources,query):
        identity=embedding_id(settings)
        if not identity:
            return {},'keyword','未配置向量服务'
        # 授权来自任务持久化快照，不信任模型传来的source id或临时函数参数。
        with db.sessions() as session:
            task=session.scalar(select(Task).where(Task.id==task_id,Task.user_id==user_id))
            if not task:
                raise ValueError('任务授权已失效')
            allowed={s.get('index_id') for s in (task.context or {}).get('sources',[]) if s.get('index_id')}
            mapping={s['index_id']:i for i,s in enumerate(sources,1) if s.get('index_id') in allowed}
            rows=session.scalars(select(SourceIndex).where(SourceIndex.id.in_(mapping),SourceIndex.user_id==user_id,
                SourceIndex.embedding_id==identity)).all()
            matches={row.id for row in rows if any(s.get('index_id')==row.id and
                hashlib.sha256(s['content'].encode()).hexdigest()==row.content_hash for s in sources)}
        if not matches:
            return {},'keyword','无匹配的数据库语义索引；旧资料可重建后用于新任务'
        vector=embed(settings,[query])[0]
        with db.sessions() as session:
            # MATERIALIZED授权子集先生成；不同模型、维度均隔离，不跨账户取top-k再过滤。
            result=session.execute(text('''WITH authorized AS MATERIALIZED (
                SELECT c.index_id,c."offset",c.embedding FROM wa_source_chunks c
                JOIN wa_source_indexes i ON i.id=c.index_id AND i.user_id=c.user_id
                WHERE c.user_id=:owner AND c.index_id=ANY(CAST(:ids AS varchar[]))
                  AND i.embedding_id=:model AND c.embedding IS NOT NULL
                  AND public.vector_dims(c.embedding)=:dim
            ) SELECT index_id,"offset",1-(embedding OPERATOR(public.<=>) CAST(:query AS public.vector)) AS score
              FROM authorized ORDER BY embedding OPERATOR(public.<=>) CAST(:query AS public.vector),index_id,"offset" LIMIT 40'''),
                {'owner':user_id,'ids':sorted(matches),'model':identity,'dim':len(vector),'query':json.dumps(vector)}).mappings().all()
        return {f"S{mapping[row['index_id']]}:{row['offset']}":float(row['score']) for row in result},'pgvector_exact','数据库授权快照内精确余弦检索'
    return rank
