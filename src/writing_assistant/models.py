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
    CANCELLED = "cancelled"


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


@dataclass(frozen=True, slots=True)
class TextVersion:
    """一篇文档在某个时刻正式保存的正文快照。"""

    id: str
    document_id: str
    version_number: int
    content: str
    created_at: str
    source_revision_id: str | None = None
    restored_from_version_id: str | None = None

    @classmethod
    def create_next(
        cls,
        *,
        document_id: str,
        version_number: int,
        content: str,
        source_revision_id: str | None = None,
        restored_from_version_id: str | None = None,
    ) -> TextVersion:
        """追加正文快照；来源是接受修订或恢复旧版，不能同时具备两种来源。"""

        if source_revision_id is not None and restored_from_version_id is not None:
            raise ValueError("正文版本不能同时来自修订和恢复")
        return cls(
            id=str(uuid4()),
            document_id=document_id,
            version_number=version_number,
            content=content,
            created_at=utc_now(),
            source_revision_id=source_revision_id,
            restored_from_version_id=restored_from_version_id,
        )

    @classmethod
    def create_initial(cls, *, document_id: str, content: str) -> TextVersion:
        """为新文档建立不可变的第一版正文。"""

        return cls(
            id=str(uuid4()),
            document_id=document_id,
            version_number=1,
            content=content,
            created_at=utc_now(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "version_number": self.version_number,
            "content": self.content,
            "created_at": self.created_at,
            "source_revision_id": self.source_revision_id,
            "restored_from_version_id": self.restored_from_version_id,
        }

    @classmethod
    def from_dict(cls, data: Any) -> TextVersion:
        if not isinstance(data, dict):
            raise ValueError("正文版本必须是对象")

        for field_name in ("id", "document_id", "content", "created_at"):
            field_value = data.get(field_name)
            if not isinstance(field_value, str):
                raise ValueError(f"正文版本的 {field_name} 必须是字符串")
            if not field_value.strip():
                raise ValueError(f"正文版本的 {field_name} 不能为空")

        version_number = data.get("version_number")
        if (
            not isinstance(version_number, int)
            or isinstance(version_number, bool)
            or version_number <= 0
        ):
            raise ValueError("正文版本的 version_number 必须是正整数")

        source_revision_id = data.get("source_revision_id")
        if source_revision_id is not None and not isinstance(source_revision_id, str):
            raise ValueError("正文版本的 source_revision_id 必须是字符串或 null")
        restored_from_version_id = data.get("restored_from_version_id")
        if restored_from_version_id is not None and (
            not isinstance(restored_from_version_id, str)
            or not restored_from_version_id.strip()
        ):
            raise ValueError("恢复来源编号必须是非空字符串或 null")
        if source_revision_id is not None and restored_from_version_id is not None:
            raise ValueError("正文版本不能同时来自修订和恢复")

        return cls(
            id=data["id"],
            document_id=data["document_id"],
            version_number=version_number,
            content=data["content"],
            created_at=data["created_at"],
            source_revision_id=source_revision_id,
            restored_from_version_id=restored_from_version_id,
        )


@dataclass(slots=True)
class Document:
    """一篇长期存在的文档，以及它当前采用的正文版本。"""

    id: str
    title: str
    current_version_id: str
    created_at: str
    updated_at: str

    @classmethod
    def create_with_initial_version(
        cls,
        *,
        title: str,
        initial_content: str,
    ) -> tuple[Document, TextVersion]:
        """同时建立文档和它的第一版正文，避免产生没有正文的文档。"""

        document_id = str(uuid4())
        version = TextVersion.create_initial(
            document_id=document_id,
            content=initial_content,
        )
        created_at = utc_now()
        document = cls(
            id=document_id,
            title=title,
            current_version_id=version.id,
            created_at=created_at,
            updated_at=created_at,
        )
        return document, version

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "title": self.title,
            "current_version_id": self.current_version_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Document:
        if not isinstance(data, dict):
            raise ValueError("文档必须是对象")

        required_fields = (
            "id",
            "title",
            "current_version_id",
            "created_at",
            "updated_at",
        )
        for field_name in required_fields:
            field_value = data.get(field_name)
            if not isinstance(field_value, str):
                raise ValueError(f"文档的 {field_name} 必须是字符串")
            if not field_value.strip():
                raise ValueError(f"文档的 {field_name} 不能为空")

        return cls(
            id=data["id"],
            title=data["title"],
            current_version_id=data["current_version_id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


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
    document_id: str | None = None
    base_version_id: str | None = None
    abandoned_at: str | None = None

    @classmethod
    def create_pending(
        cls,
        *,
        original_text: str,
        instruction: str,
        candidate: RevisionCandidate,
        model: str,
        previous_record_id: str | None = None,
        document_id: str | None = None,
        base_version_id: str | None = None,
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
            document_id=document_id,
            base_version_id=base_version_id,
        )

    def cancel(self) -> None:
        """放弃尚未决定的候选，保留原文和审计记录。"""

        self._ensure_pending()
        self.status = RevisionStatus.CANCELLED
        self.final_text = self.original_text
        self.decided_at = utc_now()

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
            "document_id": self.document_id,
            "base_version_id": self.base_version_id,
            "abandoned_at": self.abandoned_at,
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
        for field_name in ("document_id", "base_version_id", "abandoned_at"):
            value = data.get(field_name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{field_name} 必须是非空字符串或 null")
        if (data.get("document_id") is None) != (data.get("base_version_id") is None):
            raise ValueError("文档编号和基准版本编号必须同时提供")
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
            document_id=data.get("document_id"),
            base_version_id=data.get("base_version_id"),
            abandoned_at=data.get("abandoned_at"),
        )
