"""保守清理：只删除源资料已删除、超过保留期、任何任务均未引用的索引。"""
from datetime import timedelta
from sqlalchemy import delete, exists, select
from .tables import Knowledge, SourceIndex, Task, User, now


def cleanup_indexes(session, user_id, *, days=7, limit=100, apply=False):
    if not 7 <= days <= 3650 or not 1 <= limit <= 100:
        raise ValueError("保留期须为7—3650天，批次须为1—100")
    # 与创建快照、资料编辑、导入保存使用相同的账户行锁。
    owner = session.scalar(select(User).where(User.id==user_id).with_for_update())
    if not owner: raise ValueError("账户不存在")
    candidates = session.scalars(select(SourceIndex).where(SourceIndex.user_id==user_id,
        SourceIndex.created_at<now()-timedelta(days=days),
        ~exists(select(Knowledge.id).where(Knowledge.id==SourceIndex.source_id, Knowledge.user_id==user_id)))
        .order_by(SourceIndex.created_at, SourceIndex.id).limit(limit)).all()
    ids = {row.id for row in candidates}
    protected = set()
    # JSON路径由数据库按账户提取，避免加载长篇正文或向量。
    contexts = session.scalars(select(Task.context["sources"]).where(Task.user_id==user_id)).yield_per(100)
    for sources in contexts:
        for source in sources or []:
            if source.get("index_id") in ids: protected.add(source["index_id"])
    removable = sorted(ids-protected)
    if apply and removable:
        session.execute(delete(SourceIndex).where(SourceIndex.user_id==user_id, SourceIndex.id.in_(removable)))
    return {"dry_run": not apply, "examined": len(ids), "protected": len(protected),
        "eligible_ids": removable, "deleted": len(removable) if apply else 0,
        "retention_days": days, "note": "保留任务引用的索引以及仍有源资料的全部索引；不删除文档、任务或来源快照。"}
