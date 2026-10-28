"""按账户统计真实已返回用量；检查点重放去重，不将未知费用填成零。"""
from collections import Counter
from datetime import timedelta
from statistics import median
from sqlalchemy import select
from .tables import Attempt, Task, now


def task_summary(task, attempts):
    calls = {}
    skipped = set()
    skip_reasons = {"没有参考证据，未进行事实核验", "候选过长，未进行完整证据核对，请分段修订"}
    reserved = 0
    baseline_reserved = 0
    for attempt in attempts:
        usage = attempt.usage or {}
        if usage.get("strategy") in {"direct", "workflow"}:
            baseline_reserved += usage.get("reserved_calls", 0)
        else:
            reserved = max(reserved, usage.get("reserved_calls", 0))
        for i, call in enumerate(usage.get("calls", [])):
            slot = call.get("budget_slot")
            key = ("slot", slot) if slot is not None else (attempt.number, i)
            calls[key] = call
            if call.get("purpose") == "evidence_audit" and call.get("usage") is None and (usage.get("evidence_audit") or {}).get("warning") in skip_reasons:
                skipped.add(key)
    # running/interrupted任务的最后检查点可能比Attempt更完整。
    checkpoint = task.checkpoint or {}
    reserved = max(reserved, checkpoint.get("reserved_calls", 0))
    for call in checkpoint.get("calls", []):
        if call.get("budget_slot") is not None:
            key = ("slot", call["budget_slot"])
            calls[key] = call
            if call.get("purpose") == "evidence_audit" and call.get("usage") is None and (checkpoint.get("evidence_audit") or {}).get("warning") in skip_reasons:
                skipped.add(key)
    for key in skipped:
        calls.pop(key, None)
    reserved = max(0, reserved-len(skipped))
    measured = [c["usage"]["total_tokens"] for c in calls.values()
                if isinstance(c.get("usage"), dict) and isinstance(c["usage"].get("total_tokens"), int)
                and not c["usage"].get("demo")]
    tools = checkpoint.get("trace", [])
    first = attempts[0].started_at if attempts else None
    elapsed = int((task.finished_at-task.created_at).total_seconds()*1000) if task.finished_at else None
    return {"request_id": (task.context or {}).get("request_id"),
        "queue_ms": max(0, int((first-task.created_at).total_seconds()*1000)) if first else None,
        "elapsed_ms": elapsed, "execution_ms": sum(a.duration_ms or 0 for a in attempts),
        "model_ms": sum(c.get("duration_ms", 0) for c in calls.values()),
        "save_flush_ms": sum((a.usage or {}).get("save_flush_ms", 0) for a in attempts),
        "tool_ms": sum(t.get("duration_ms", 0) for t in tools),
        "retrieval_ms": sum(t.get("duration_ms", 0) for t in tools if t.get("tool") in {"search_sources", "read_source"}),
        "returned_calls": len(calls), "reserved_calls": reserved + baseline_reserved,
        "unconfirmed_calls": max(0, reserved + baseline_reserved-len(calls)), "reported_tokens": sum(measured),
        "metered_calls": len(measured), "unmetered_calls": len(calls)-len(measured),
        "retries": max(0, len(attempts)-1), "amount": None,
        "failure_stage": "evidence" if task.error_code == "evidence_needed" else "generation" if task.error_code else None,
        "fallbacks": sum(1 for h in checkpoint.get("retrieval_history", []) if "降级" in str(h.get("note", ""))),
        "note": "Token仅统计供应商已返回用量；中断未返回、搜索和Embedding费用不在此计数。金额未配置，耗时可能部分重叠。"}


def account_summary(session, user_id, days):
    # 分批读取大上下文；9000是总统计上限，不能一次装入9000份正文/检查点。
    stream = session.scalars(select(Task).where(Task.user_id==user_id, Task.created_at>=now()-timedelta(days=days))
        .order_by(Task.created_at.desc(), Task.id).limit(9001)).yield_per(20)
    rows, statuses = [], Counter()
    count = 0
    truncated = False
    while True:
        batch = stream.fetchmany(20)
        if not batch: break
        if count+len(batch)>9000:
            truncated = True
            batch = batch[:9000-count]
        if not batch: break
        attempts = session.scalars(select(Attempt).join(Task).where(Task.user_id==user_id,
            Task.id.in_([t.id for t in batch])).order_by(Attempt.number)).all()
        grouped = {}
        for attempt in attempts: grouped.setdefault(attempt.task_id, []).append(attempt)
        for task in batch:
            rows.append(task_summary(task, grouped.get(task.id, [])))
            statuses[task.status] += 1
        count += len(batch)
        if truncated: break
    stream.close()
    durations = sorted(r["elapsed_ms"] for r in rows if r["elapsed_ms"] is not None)
    decided = statuses["accepted"]+statuses["rejected"]
    return {"days": days, "tasks": count, "truncated": truncated, "statuses": dict(statuses),
        "acceptance_rate": statuses["accepted"]/decided if decided else None,
        "acceptance_denominator": decided, "median_elapsed_ms": median(durations) if durations else None,
        "p95_elapsed_ms": durations[max(0, (95*len(durations)+99)//100-1)] if durations else None,
        **{key: sum(r[key] for r in rows) for key in ("reported_tokens", "returned_calls", "unconfirmed_calls", "unmetered_calls", "retries", "fallbacks")},
        "amount": None, "note": "按任务创建时间统计，最多90天9000条；接受率分母仅含明确接受或拒绝的任务，不是模型准确率。Token不含搜索/Embedding及未返回用量。"}
