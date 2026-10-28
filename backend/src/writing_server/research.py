"""资料导入作业：数据库投递意图、领取令牌、取消栅栏、来源快照与去重。"""
import base64
from datetime import timedelta
import hashlib
from pathlib import PurePath

from fastapi import HTTPException
from sqlalchemy import select, func

from .extraction import extract
from .network import fetch_page, public_url
from .retrieval import build_index
from .vector_store import persist_index
from .tables import ResearchJob, Knowledge, User, now, uid


def job_data(row):
    return {k:getattr(row,k) for k in ('id','kind','title','status','progress','error','knowledge_id','created_at','finished_at','attempts')}


def owned(session, user_id, job_id, lock=False):
    stmt = select(ResearchJob).where(ResearchJob.id==job_id, ResearchJob.user_id==user_id)
    row = session.scalar(stmt.with_for_update() if lock else stmt)
    if not row:
        raise HTTPException(404, '找不到导入任务')
    return row


def enqueue(session, user_id, data):
    session.scalar(select(User).where(User.id==user_id).with_for_update())
    active = session.scalar(select(func.count()).select_from(ResearchJob).where(ResearchJob.user_id==user_id, ResearchJob.status.in_(['queued','running'])))
    daily = session.scalar(select(func.count()).select_from(ResearchJob).where(ResearchJob.user_id==user_id, ResearchJob.created_at>now()-timedelta(days=1)))
    if active >= 2 or daily >= 30:
        raise HTTPException(429,'最多同时导入2项，每日最多30项')
    payload = data.model_dump()
    try:
        if data.kind == 'url':
            payload['url'] = public_url(data.url)
        elif data.kind == 'file':
            if PurePath(data.filename).suffix.lower() not in {'.txt','.md','.pdf'}:
                raise ValueError('支持TXT、Markdown和文字PDF')
            raw = base64.b64decode(data.content_base64, validate=True)
            if not 0 < len(raw) <= 2_000_000:
                raise ValueError('文件大小须为1字节至2MB')
            payload['raw_hash'] = hashlib.sha256(raw).hexdigest()
        else:
            row = session.scalar(select(Knowledge).where(Knowledge.id==data.knowledge_id,Knowledge.user_id==user_id,Knowledge.kind=='source'))
            if not row:
                raise HTTPException(404,'找不到待重建索引的资料')
            payload = {'knowledge_id': row.id, 'content':row.content, 'provenance':row.provenance,
                       'hash':hashlib.sha256(row.content.encode()).hexdigest()}
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    row = ResearchJob(user_id=user_id, kind=data.kind, title=data.title, payload=payload)
    session.add(row);session.flush()
    return row


def cancel(session, user_id, job_id):
    row=owned(session,user_id,job_id,True)
    if row.status not in {'queued','running','cancelled'}:
        raise HTTPException(409,'已结束的导入不能取消')
    row.status,row.claim_token,row.finished_at,row.payload='cancelled',None,now(),{}
    return row


def dispatch(db,sender):
    with db.sessions.begin() as session:
        expired=session.scalars(select(ResearchJob).where(ResearchJob.status=='running',ResearchJob.lease_until<now()).with_for_update(skip_locked=True)).all()
        for row in expired:
            row.claim_token=None
            if row.attempts>=2:
                row.status,row.error,row.finished_at,row.payload='failed','导入多次中断，已停止',now(),{}
            else:
                row.status,row.dispatch_after='queued',now()
        rows=session.scalars(select(ResearchJob).where(ResearchJob.status=='queued',ResearchJob.dispatch_after<=now()).with_for_update(skip_locked=True).limit(10)).all()
        for row in rows:
            try:
                sender(row.id)
            except Exception:
                row.dispatch_after=now()+timedelta(seconds=10)
            else:
                row.dispatch_after=now()+timedelta(seconds=30)


def process(db, settings, job_id):
    token=uid()
    with db.sessions.begin() as session:
        row=session.scalar(select(ResearchJob).where(ResearchJob.id==job_id).with_for_update())
        if not row or row.status!='queued':
            return False
        row.status,row.claim_token,row.lease_until='running',token,now()+timedelta(seconds=settings.lease_seconds)
        row.attempts+=1;row.progress='正在读取资料'
        payload,kind,user_id,title=row.payload,row.kind,row.user_id,row.title
    def progress(message):
        with db.sessions.begin() as session:
            row=session.scalar(select(ResearchJob).where(ResearchJob.id==job_id).with_for_update())
            if row.status!='running' or row.claim_token!=token or row.lease_until<=now():
                raise ValueError('任务已取消或执行权已过期')
            row.progress=message
    try:
        if kind=='reindex':
            content=payload['content'];provenance=payload['provenance'];pages=provenance.get('pages',[])
        else:
            if kind=='url':
                raw,mime,url=fetch_page(payload['url'])
                name='web.pdf' if mime=='application/pdf' else 'web.txt'
            else:
                raw=base64.b64decode(payload['content_base64']);mime='';url=None;name=payload['filename']
            progress('正在提取文字')
            parsed=extract(raw,name,mime)
            content,pages=parsed['content'],parsed['pages']
            provenance={'kind':kind,'url':url,'filename':name,'fetched_at':now().isoformat(),
                        'raw_sha256':hashlib.sha256(raw).hexdigest(),'pages':pages}
        progress('正在建立检索索引')
        index=build_index(settings,content,pages)
        progress('正在保存来源快照')
        with db.sessions.begin() as session:
            session.scalar(select(User).where(User.id==user_id).with_for_update())
            row=session.scalar(select(ResearchJob).where(ResearchJob.id==job_id).with_for_update())
            if row.status!='running' or row.claim_token!=token or row.lease_until<=now():
                return False
            if kind=='reindex':
                entry=session.scalar(select(Knowledge).where(Knowledge.id==payload['knowledge_id'],Knowledge.user_id==user_id).with_for_update())
                if not entry or hashlib.sha256(entry.content.encode()).hexdigest()!=payload['hash']:
                    raise ValueError('资料已编辑或删除，旧索引结果未覆盖新内容')
            else:
                existing=session.scalars(select(Knowledge).where(Knowledge.user_id==user_id,Knowledge.kind=='source')).all()
                entry=next((item for item in existing if item.content==content and (item.provenance or {}).get('url')==provenance.get('url')),None)
                if entry is None:
                    if session.scalar(select(func.count()).select_from(Knowledge).where(Knowledge.user_id==user_id))>=100:
                        raise ValueError('资料与记忆已达100项上限')
                    entry=Knowledge(id=uid(),user_id=user_id,kind='source',title=title,content=content,provenance=provenance)
                    session.add(entry)
            entry.chunk_index=index;entry.updated_at=now()
            session.flush()
            persist_index(session,user_id,{'id':entry.id,'title':entry.title,'content':entry.content,'chunk_index':index})
            row.status,row.progress,row.knowledge_id,row.finished_at='succeeded','已保存，可勾选用于修订',entry.id,now()
            row.payload,row.claim_token={},None
        return True
    except Exception as exc:
        message=str(exc) if isinstance(exc,ValueError) else '导入或索引服务失败，请检查配置后重新提交'
        with db.sessions.begin() as session:
            row=session.scalar(select(ResearchJob).where(ResearchJob.id==job_id).with_for_update())
            if row.status=='running' and row.claim_token==token:
                row.status,row.error,row.payload,row.claim_token,row.finished_at='failed',message[:300],{},None,now()
        return False
