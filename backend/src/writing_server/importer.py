"""旧 JSON 一次性导入；只读源文件，事务失败整体回滚，同账户可去重。"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import insert, select
from writing_assistant.storage.json_store import JsonProjectStore

from .tables import Candidate, Document, ImportBatch, Task, User, Version, now, uid


def legacy_time(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def import_json(db, path: Path, username: str):
    if path.stat().st_size > 10_000_000:
        raise ValueError("旧文件过大，请先拆分或检查文件")
    raw = path.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    # 复用原存储的结构与引用校验，只调用只读读取，不触发旧文件迁移写入。
    store = JsonProjectStore(path)
    data = store._load_data()
    if path.read_bytes() != raw:
        raise ValueError("读取期间旧文件发生变化，请停止其他写入后重试")
    document_ids = {row.id: uid() for row in data.documents}
    version_ids = {row.id: uid() for row in data.text_versions}
    task_ids = {row.id: uid() for row in data.revisions}
    by_id = {row.id: row for row in data.revisions}
    with db.sessions.begin() as session:
        user = session.scalar(select(User).where(User.username == username).with_for_update())
        if not user:
            raise ValueError("请先注册接收旧资料的网页账户")
        existing = session.scalar(
            select(ImportBatch).where(ImportBatch.user_id == user.id, ImportBatch.source_hash == fingerprint)
        )
        if existing:
            return {"message": "该文件已经导入，未重复创建", "mapping": existing.mapping}
        if len(data.documents) + len(data.revisions) > 1000:
            raise ValueError("单次导入对象过多")
        for row in data.documents:
            session.execute(
                insert(Document).values(
                    id=document_ids[row.id],
                    user_id=user.id,
                    title=row.title,
                    current_version_id=version_ids[row.current_version_id],
                    created_at=legacy_time(row.created_at),
                    updated_at=legacy_time(row.updated_at),
                )
            )
        for row in data.text_versions:
            session.execute(
                insert(Version).values(
                    id=version_ids[row.id],
                    document_id=document_ids[row.document_id],
                    number=row.version_number,
                    content=row.content,
                    kind="imported",
                    created_at=legacy_time(row.created_at),
                    source_task_id=task_ids.get(row.source_revision_id),
                    restored_from_id=version_ids.get(row.restored_from_version_id),
                )
            )
        for row in data.revisions:
            doc_id = document_ids.get(row.document_id)
            base_id = version_ids.get(row.base_version_id)
            if not doc_id:
                # v0.1 的独立修订：每条记录建立明确文档归属，不丢失原文。
                doc_id, base_id = uid(), uid()
                session.execute(
                    insert(Document).values(
                        id=doc_id,
                        user_id=user.id,
                        title=f"导入修订 {row.id[:8]}",
                        current_version_id=base_id,
                        created_at=legacy_time(row.created_at),
                        updated_at=legacy_time(row.decided_at or row.created_at),
                    )
                )
                session.execute(
                    insert(Version).values(
                        id=base_id,
                        document_id=doc_id,
                        number=1,
                        content=row.original_text,
                        kind="imported",
                        created_at=legacy_time(row.created_at),
                    )
                )
            status = {"pending": "review"}.get(row.status.value, row.status.value)
            chain = []
            previous = by_id.get(row.previous_record_id)
            previous_id = (
                task_ids.get(previous.id)
                if previous and previous.document_id == row.document_id and row.document_id
                else None
            )
            visited = set()
            while previous and previous.document_id == row.document_id and previous.id not in visited:
                visited.add(previous.id)
                if previous.status.value == "rejected":
                    chain.append(
                        {
                            "text": previous.candidate.text,
                            "reason": previous.candidate.reason,
                            "feedback": previous.rejection_reason or "旧版未记录原因",
                        }
                    )
                previous = by_id.get(previous.previous_record_id)
            task = Task(
                id=task_ids[row.id],
                user_id=user.id,
                document_id=doc_id,
                base_version_id=base_id,
                instruction=row.instruction or "旧版未记录修改要求",
                feedback=list(reversed(chain))[-4:],
                status=status,
                previous_task_id=previous_id,
                model=row.model,
                provider="imported",
                prompt_version="legacy-import",
                idempotency_key="import_" + task_ids[row.id],
                request_hash=fingerprint,
                rejection_reason=row.rejection_reason,
                created_at=legacy_time(row.created_at),
                decided_at=legacy_time(row.decided_at),
            )
            session.add(task)
            session.flush()
            session.add(Candidate(task_id=task.id, text=row.candidate.text, reason=row.candidate.reason))
            if not row.document_id and status == "accepted":
                final_id = uid()
                session.execute(
                    insert(Version).values(
                        id=final_id,
                        document_id=doc_id,
                        number=2,
                        content=row.final_text or row.candidate.text,
                        kind="imported",
                        source_task_id=task.id,
                        created_at=now(),
                    )
                )
                session.get(Document, doc_id).current_version_id = final_id
        mapping = {"documents": document_ids, "versions": version_ids, "revisions": task_ids}
        session.add(ImportBatch(user_id=user.id, source_hash=fingerprint, mapping=mapping))
    return {"message": "导入完成，原文件保持不变", "mapping": mapping}
