"""项目中的核心数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    """返回带时区的 UTC 时间，便于不同机器稳定比较。"""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RevisionStatus(str, Enum):
    """一次修订所处的状态。"""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RevisionCandidate:
    """模型生成的一条修改候选。"""

    text: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"text": self.text, "reason": self.reason}

    @classmethod
    def from_dict(cls, data: Any) -> RevisionCandidate:
        if not isinstance(data, dict):
            raise ValueError("candidate 必须是对象")
        text = data.get("text")
        reason = data.get("reason")
        if not isinstance(text, str) or not isinstance(reason, str):
            raise ValueError("candidate.text 和 candidate.reason 必须是字符串")
        return cls(text=text, reason=reason)


@dataclass(frozen=True, slots=True)
class RevisionFeedback:
    """一条被拒绝候选及使用者给出的修改反馈。"""

    rejected_candidate: RevisionCandidate
    user_feedback: str


@dataclass(frozen=True, slots=True)
class RevisionRequest:
    """使用者提交的一次修订请求及其反馈历史。"""

    source_text: str
    instruction: str
    feedback_history: tuple[RevisionFeedback, ...] = ()


@dataclass(slots=True)
class RevisionRecord:
    """从原文到人工决定的一次完整修订记录。"""

    id: str
    original_text: str
    candidate: RevisionCandidate
    model: str
    status: RevisionStatus
    created_at: str
    decided_at: str | None = None
    final_text: str | None = None
    instruction: str | None = None
    rejection_reason: str | None = None
    previous_record_id: str | None = None

    @classmethod
    def create_pending(
        cls,
        *,
        original_text: str,
        instruction: str,
        candidate: RevisionCandidate,
        model: str,
        previous_record_id: str | None = None,
    ) -> RevisionRecord:
        return cls(
            id=str(uuid4()),
            original_text=original_text,
            instruction=instruction,
            candidate=candidate,
            model=model,
            status=RevisionStatus.PENDING,
            created_at=utc_now(),
            previous_record_id=previous_record_id,
        )

    def accept(self) -> None:
        self._ensure_pending()
        self.status = RevisionStatus.ACCEPTED
        self.final_text = self.candidate.text
        self.decided_at = utc_now()

    def reject(self, rejection_reason: str) -> None:
        self._ensure_pending()
        self.status = RevisionStatus.REJECTED
        self.final_text = self.original_text
        self.rejection_reason = rejection_reason
        self.decided_at = utc_now()

    def _ensure_pending(self) -> None:
        if self.status is not RevisionStatus.PENDING:
            raise ValueError("只有 pending 记录可以接受或拒绝")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "original_text": self.original_text,
            "instruction": self.instruction,
            "rejection_reason": self.rejection_reason,
            "previous_record_id": self.previous_record_id,
            "candidate": self.candidate.to_dict(),
            "final_text": self.final_text,
            "model": self.model,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> RevisionRecord:
        if not isinstance(data, dict):
            raise ValueError("修订记录必须是对象")

        for field_name in ("id", "original_text", "model", "created_at"):
            if not isinstance(data.get(field_name), str):
                raise ValueError(f"{field_name} 必须是字符串")

        try:
            status = RevisionStatus(data.get("status"))
        except ValueError as exc:
            raise ValueError("status 不是支持的状态") from exc

        decided_at = data.get("decided_at")
        final_text = data.get("final_text")
        instruction = data.get("instruction")
        rejection_reason = data.get("rejection_reason")
        previous_record_id = data.get("previous_record_id")
        if decided_at is not None and not isinstance(decided_at, str):
            raise ValueError("decided_at 必须是字符串或 null")
        if final_text is not None and not isinstance(final_text, str):
            raise ValueError("final_text 必须是字符串或 null")
        if instruction is not None and not isinstance(instruction, str):
            raise ValueError("instruction 必须是字符串或 null")
        if rejection_reason is not None and not isinstance(rejection_reason, str):
            raise ValueError("rejection_reason 必须是字符串或 null")
        if previous_record_id is not None and not isinstance(previous_record_id, str):
            raise ValueError("previous_record_id 必须是字符串或 null")

        return cls(
            id=data["id"],
            original_text=data["original_text"],
            candidate=RevisionCandidate.from_dict(data.get("candidate")),
            model=data["model"],
            status=status,
            created_at=data["created_at"],
            decided_at=decided_at,
            final_text=final_text,
            instruction=instruction,
            rejection_reason=rejection_reason,
            previous_record_id=previous_record_id,
        )
