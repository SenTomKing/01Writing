"""数据库保存投递意图、领取租约和结果；队列消息允许重复。"""

from datetime import timedelta
import time

from sqlalchemy import delete, select

from .generation import ModelFailure, generate
from .services import event
from .tables import Attempt, Candidate, LoginSession, RateBucket, Task, Version, now, uid


def recover(db, settings):
    count = 0
    with db.sessions.begin() as session:
        expired = session.scalars(
            select(Task)
            .where(Task.status == "running", Task.lease_until < now())
            .with_for_update(skip_locked=True)
            .limit(50)
        ).all()
        for task in expired:
            attempt = session.scalar(select(Attempt).where(Attempt.claim_token == task.claim_token))
            if attempt:
                attempt.outcome, attempt.finished_at, attempt.error_code = "interrupted", now(), "lease_expired"
            task.claim_token, task.lease_until = None, None
            if task.attempts >= settings.max_attempts:
                task.status, task.error_code, task.error_message = (
                    "failed",
                    "attempt_limit",
                    "任务多次中断，已停止自动重试",
                )
                task.finished_at = now()
            else:
                task.status, task.dispatch_after = "queued", now()
            event(
                session,
                task,
                "recovered",
                message="执行中断，已重新排队" if task.status == "queued" else task.error_message,
            )
            count += 1
        session.execute(delete(LoginSession).where(LoginSession.expires_at < now()))
        session.execute(delete(RateBucket).where(RateBucket.expires_at < now()))
    return count


def dispatch(db, sender):
    """保存的 queued 任务就是待投递意图；发出后未被领取仍会再投递。"""
    sent = 0
    with db.sessions.begin() as session:
        tasks = session.scalars(
            select(Task)
            .where(Task.status == "queued", Task.dispatch_after <= now())
            .order_by(Task.created_at)
            .with_for_update(skip_locked=True)
            .limit(20)
        ).all()
        for task in tasks:
            try:
                sender(task.id)
            except Exception:
                # 不把 broker 地址、密码或供应商异常原文记录到事件。
                task.dispatch_after = now() + timedelta(seconds=5)
                continue
            task.dispatch_after = now() + timedelta(seconds=30)
            sent += 1
    return sent


def process_task(db, settings, task_id, generator=generate):
    token = uid()
    with db.sessions.begin() as session:
        task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        if not task or task.status != "queued":
            return False
        if task.attempts >= settings.max_attempts:
            task.status, task.error_code, task.error_message, task.finished_at = (
                "failed",
                "attempt_limit",
                "已达到重试上限",
                now(),
            )
            event(session, task, "failed", message=task.error_message)
            return False
        task.status, task.claim_token = "running", token
        task.attempts += 1
        task.lease_until = now() + timedelta(seconds=settings.lease_seconds)
        session.add(
            Attempt(
                id=uid(),
                task_id=task.id,
                number=task.attempts,
                claim_token=token,
                provider=task.provider,
                model=task.model,
                prompt_version=task.prompt_version,
            )
        )
        event(session, task, "running", message="正在生成候选", attempt=task.attempts)
        version = session.get(Version, task.base_version_id)
        arguments = dict(
            source=version.content,
            instruction=task.instruction,
            feedback=task.feedback,
            provider=task.provider,
            model=task.model,
        )
    started = time.monotonic()
    result, failure = None, None
    try:
        result = generator(settings, **arguments)
        from writing_assistant.validation import validate_candidate

        validate_candidate(result.candidate, max_text_chars=20_000)
    except ModelFailure as exc:
        failure = exc
    except Exception:
        failure = ModelFailure("generation_failed", "生成过程失败，请稍后重试")
    duration = int((time.monotonic() - started) * 1000)
    with db.sessions.begin() as session:
        task = session.scalar(select(Task).where(Task.id == task_id).with_for_update())
        attempt = session.scalar(select(Attempt).where(Attempt.claim_token == token))
        attempt.duration_ms, attempt.finished_at = duration, now()
        attempt.usage = result.usage if result else failure.usage
        if task.status != "running" or task.claim_token != token or task.lease_until <= now():
            attempt.outcome, attempt.error_code = "discarded", "claim_lost"
            return False
        task.claim_token, task.lease_until = None, None
        if failure:
            attempt.outcome, attempt.error_code = "failed", failure.code
            task.error_code, task.error_message = failure.code, failure.message
            if failure.retryable and task.attempts < settings.max_attempts:
                task.status = "queued"
                task.dispatch_after = now() + timedelta(seconds=task.attempts * 5)
                event(session, task, "retrying", message="临时错误，等待有限重试")
            else:
                task.status, task.finished_at = "failed", now()
                event(session, task, "failed", message=failure.message)
        else:
            # 在边界再次校验，测试／其他供应商也不能绕过输出约束。
            candidate = validate_candidate(result.candidate, max_text_chars=20_000)
            session.add(Candidate(task_id=task.id, text=candidate.text, reason=candidate.reason))
            task.status, task.finished_at, task.error_code, task.error_message = "review", now(), None, None
            attempt.outcome = "succeeded"
            event(session, task, "review", message="候选已保存，等待人工确认")
    return True
