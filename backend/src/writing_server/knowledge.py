"""账户隔离的资料和显式记忆；检索先用可复现的字词匹配基线。"""

import re
from datetime import timezone

from fastapi import HTTPException
from sqlalchemy import func, select

from .tables import Knowledge, User, now


def data(row):
    return {
        key: getattr(row, key) for key in ("id", "kind", "title", "content", "expires_at", "created_at", "updated_at")
    }


def entries(session, user_id, kind=None):
    query = select(Knowledge).where(Knowledge.user_id == user_id)
    if kind:
        query = query.where(Knowledge.kind == kind)
    return session.scalars(query.order_by(Knowledge.updated_at.desc(), Knowledge.id).limit(100)).all()


def save(session, user_id, values, entry_id=None):
    session.scalar(select(User).where(User.id == user_id).with_for_update())
    if values.kind == "memory" and len(values.content) > 2000:
        raise HTTPException(422, "单条记忆不能超过 2000 字符")
    if entry_id:
        row = session.scalar(select(Knowledge).where(Knowledge.id == entry_id, Knowledge.user_id == user_id))
        if not row:
            raise HTTPException(404, "找不到资料或记忆")
        if row.kind != values.kind:
            raise HTTPException(422, "不能改变资料类型")
    else:
        if session.scalar(select(func.count()).select_from(Knowledge).where(Knowledge.user_id == user_id)) >= 100:
            raise HTTPException(429, "每个账户最多保存 100 项资料与记忆")
        row = Knowledge(user_id=user_id)
        session.add(row)
    for name in ("kind", "title", "content", "expires_at"):
        setattr(row, name, getattr(values, name))
    row.updated_at = now()
    session.flush()
    return row


def snapshot(session, user_id, source_ids, memory_ids):
    if len(set(source_ids)) != len(source_ids) or len(set(memory_ids)) != len(memory_ids):
        raise HTTPException(422, "不能重复选择同一条资料或记忆")
    result = {"sources": [], "memories": []}
    for key, ids, kind in [("sources", source_ids, "source"), ("memories", memory_ids, "memory")]:
        for entry_id in ids:
            row = session.scalar(select(Knowledge).where(Knowledge.id == entry_id, Knowledge.user_id == user_id))
            if not row or row.kind != kind:
                raise HTTPException(404, "所选资料或记忆不可用")
            expiry = row.expires_at
            if expiry and expiry.replace(tzinfo=expiry.tzinfo or timezone.utc) <= now():
                raise HTTPException(409, "所选记忆已过期，请更新或取消选择")
            result[key].append(
                {"id": row.id, "title": row.title, "content": row.content, "updated_at": row.updated_at.isoformat()}
            )
    return result


def terms(text):
    text = text.casefold()
    words = set(re.findall(r"[a-z0-9_]+", text))
    for segment in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(segment[i : i + 2] for i in range(max(1, len(segment) - 1)))
    return words


def chunks(sources):
    # 固定位置引用可追溯到任务创建时的不可变快照。
    for index, source in enumerate(sources, 1):
        for offset in range(0, len(source["content"]), 700):
            yield {
                "ref": f"S{index}:{offset}",
                "title": source["title"],
                "offset": offset,
                "text": source["content"][offset : offset + 900],
            }


def search(sources, query):
    query_terms = terms(query)
    ranked = []
    for chunk in chunks(sources):
        score = len(query_terms & terms(chunk["title"] + " " + chunk["text"]))
        if score:
            ranked.append((score, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1]["ref"]))
    return [chunk for _, chunk in ranked[:3]]
