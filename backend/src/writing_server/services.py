"""短事务中的业务规则；调用方在 session.begin 内提交。"""

from datetime import timedelta
import json

from fastapi import HTTPException
from sqlalchemy import func, insert, select

from .security import digest
from .tables import Attempt, Candidate, Document, Task, TaskEvent, User, Version, now, uid

ACTIVE = ("queued", "running")
TERMINAL = ("accepted", "rejected", "failed", "cancelled")


def event(session, task, kind, **data):
    session.add(TaskEvent(task_id=task.id, kind=kind, data=data))


def owned_document(session, user_id, document_id, *, lock=False):
    statement = select(Document).where(Document.id == document_id, Document.user_id == user_id)
    if lock:
        statement = statement.with_for_update()
    document = session.scalar(statement)
    if document is None:
        raise HTTPException(404, "找不到文档")
    return document


def owned_task(session, user_id, task_id, *, lock=False):
    statement = select(Task).where(Task.id == task_id, Task.user_id == user_id)
    if lock:
        statement = statement.with_for_update()
    task = session.scalar(statement)
    if task is None:
        raise HTTPException(404, "找不到任务")
    return task


def version_data(version):
    return {
        "id": version.id,
        "document_id": version.document_id,
        "number": version.number,
        "content": version.content,
        "kind": version.kind,
        "created_at": version.created_at,
        "source_task_id": version.source_task_id,
        "restored_from_id": version.restored_from_id,
    }


def document_data(session, document):
    return {
        "id": document.id,
        "title": document.title,
        "current_version_id": document.current_version_id,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "current_version": version_data(session.get(Version, document.current_version_id)),
    }


def task_data(session, task):
    candidate = session.get(Candidate, task.id)
    attempts = session.scalars(select(Attempt).where(Attempt.task_id == task.id).order_by(Attempt.number)).all()
    return {
        "id": task.id,
        "document_id": task.document_id,
        "base_version_id": task.base_version_id,
        "source_text": session.get(Version, task.base_version_id).content,
        "instruction": task.instruction,
        "status": task.status,
        "attempts": task.attempts,
        "previous_task_id": task.previous_task_id,
        "rejection_reason": task.rejection_reason,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "finished_at": task.finished_at,
        "decided_at": task.decided_at,
        "candidate": {"text": candidate.text, "reason": candidate.reason} if candidate else None,
        "runs": [
            {
                "number": row.number,
                "provider": row.provider,
                "model": row.model,
                "prompt_version": row.prompt_version,
                "duration_ms": row.duration_ms,
                "usage": row.usage,
                "outcome": row.outcome,
                "error_code": row.error_code,
            }
            for row in attempts
        ],
    }


def create_document(session, user_id, title, content):
    # 锁定用户，避免并发请求绕过文档数量限制。
    session.scalar(select(User).where(User.id == user_id).with_for_update())
    if session.scalar(select(func.count()).select_from(Document).where(Document.user_id == user_id)) >= 200:
        raise HTTPException(429, "每个账户最多保存 200 篇文档")
    doc_id, version_id = uid(), uid()
    session.execute(
        insert(Document).values(
            id=doc_id, user_id=user_id, title=title, current_version_id=version_id, created_at=now(), updated_at=now()
        )
    )
    session.execute(
        insert(Version).values(
            id=version_id, document_id=doc_id, number=1, content=content, kind="initial", created_at=now()
        )
    )
    return session.get(Document, doc_id)


def append_version(
    session, document, content, *, base_version_id, kind="edit", source_task_id=None, restored_from_id=None
):
    if document.current_version_id != base_version_id:
        raise HTTPException(409, "正文已发生变化，请刷新后重新操作")
    previous = session.get(Version, document.current_version_id)
    version = Version(
        id=uid(),
        document_id=document.id,
        number=previous.number + 1,
        content=content,
        kind=kind,
        source_task_id=source_task_id,
        restored_from_id=restored_from_id,
    )
    session.add(version)
    session.flush()
    document.current_version_id = version.id
    document.updated_at = now()
    session.flush()
    return version


def create_task(session, user_id, document_id, instruction, base_version_id, key, settings, *, previous_task_id=None):
    # 统一锁顺序：用户 → 文档 → 任务；并发幂等请求在用户行串行。
    session.scalar(select(User).where(User.id == user_id).with_for_update())
    fingerprint = digest(json.dumps([document_id, instruction, base_version_id, previous_task_id], ensure_ascii=False))
    existing = session.scalar(select(Task).where(Task.user_id == user_id, Task.idempotency_key == key))
    if existing:
        if existing.request_hash != fingerprint:
            raise HTTPException(409, "相同幂等键不能用于不同请求")
        return existing
    document = owned_document(session, user_id, document_id, lock=True)
    if document.current_version_id != base_version_id:
        raise HTTPException(409, "正文已更新，请刷新后生成")
    active_count = session.scalar(
        select(func.count()).select_from(Task).where(Task.user_id == user_id, Task.status.in_(ACTIVE))
    )
    if active_count >= settings.max_active_tasks:
        raise HTTPException(429, "进行中的任务过多，请等待或取消")
    day_count = session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.user_id == user_id, Task.created_at >= now() - timedelta(days=1))
    )
    if day_count >= settings.daily_tasks:
        raise HTTPException(429, "已达到每日任务上限")
    feedback = []
    if previous_task_id:
        previous = owned_task(session, user_id, previous_task_id, lock=True)
        if (
            previous.document_id != document_id
            or previous.base_version_id != base_version_id
            or previous.status != "rejected"
        ):
            raise HTTPException(409, "只能继续同一正文版本上已拒绝的候选")
        candidate = session.get(Candidate, previous.id)
        if not candidate:
            raise HTTPException(409, "旧任务没有可用候选")
        instruction = previous.instruction
        feedback = [
            *previous.feedback,
            {"text": candidate.text, "reason": candidate.reason, "feedback": previous.rejection_reason},
        ][-4:]
    task = Task(
        id=uid(),
        user_id=user_id,
        document_id=document_id,
        base_version_id=base_version_id,
        instruction=instruction,
        idempotency_key=key,
        request_hash=fingerprint,
        previous_task_id=previous_task_id,
        feedback=feedback,
        provider=settings.provider,
        model=settings.model if settings.provider == "deepseek" else "demo-v1",
        prompt_version="revision-v1",
    )
    session.add(task)
    session.flush()
    event(session, task, "queued", message="任务已保存，等待执行")
    return task


def decide(session, user_id, task_id, action, reason=None):
    # 先从无锁快照找到文档，再按统一顺序加锁并重新验证任务。
    initial = owned_task(session, user_id, task_id)
    document = owned_document(session, user_id, initial.document_id, lock=True)
    task = owned_task(session, user_id, task_id, lock=True)
    session.refresh(task)
    target = "accepted" if action == "accept" else "rejected"
    if task.status == target:
        return task
    if task.status != "review":
        raise HTTPException(409, "该任务当前不能接受或拒绝")
    if action == "accept":
        candidate = session.get(Candidate, task.id)
        append_version(
            session,
            document,
            candidate.text,
            base_version_id=task.base_version_id,
            kind="accepted",
            source_task_id=task.id,
        )
    else:
        task.rejection_reason = reason
    task.status, task.decided_at = target, now()
    event(session, task, target, message="已接受修改" if action == "accept" else "已保存拒绝原因")
    return task


def cancel(session, user_id, task_id):
    task = owned_task(session, user_id, task_id, lock=True)
    if task.status == "cancelled":
        return task
    if task.status not in (*ACTIVE, "review"):
        raise HTTPException(409, "已结束的任务不能取消")
    task.status, task.finished_at = "cancelled", now()
    task.claim_token = None
    event(session, task, "cancelled", message="任务已取消，迟到结果不会保存为候选")
    return task
