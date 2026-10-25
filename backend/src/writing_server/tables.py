"""PostgreSQL 数据模型：任务执行、人工决定和正式正文分别保存。"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "wa_users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginSession(Base):
    __tablename__ = "wa_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("wa_users.id"), index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RateBucket(Base):
    __tablename__ = "wa_rate_buckets"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Document(Base):
    __tablename__ = "wa_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "current_version_id"],
            ["wa_versions.document_id", "wa_versions.id"],
            name="wa_document_current_fk",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("length(trim(title)) BETWEEN 1 AND 200", name="wa_document_title"),
        Index("wa_documents_owner_updated", "user_id", "updated_at", "id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("wa_users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    current_version_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Version(Base):
    __tablename__ = "wa_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "id", name="wa_version_document_id"),
        UniqueConstraint("document_id", "number", name="wa_version_number"),
        CheckConstraint("number > 0 AND length(trim(content)) > 0", name="wa_version_content"),
        CheckConstraint("kind IN ('initial','edit','accepted','restored','imported')", name="wa_version_kind"),
        ForeignKeyConstraint(
            ["document_id", "restored_from_id"],
            ["wa_versions.document_id", "wa_versions.id"],
            name="wa_version_restore_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["document_id", "source_task_id"],
            ["wa_tasks.document_id", "wa_tasks.id"],
            name="wa_version_source_task_fk",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("wa_documents.id", deferrable=True, initially="DEFERRED"))
    number: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))
    source_task_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    restored_from_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Task(Base):
    __tablename__ = "wa_tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="wa_task_idempotency"),
        UniqueConstraint("document_id", "id", name="wa_task_document_id"),
        CheckConstraint(
            "status IN ('queued','running','review','accepted','rejected','failed','cancelled')", name="wa_task_status"
        ),
        CheckConstraint("attempts >= 0", name="wa_task_attempts"),
        ForeignKeyConstraint(
            ["document_id", "base_version_id"], ["wa_versions.document_id", "wa_versions.id"], name="wa_task_base_fk"
        ),
        ForeignKeyConstraint(
            ["document_id", "previous_task_id"],
            ["wa_tasks.document_id", "wa_tasks.id"],
            name="wa_task_previous_fk",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("wa_task_dispatch", "status", "dispatch_after"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("wa_users.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("wa_documents.id"), index=True)
    base_version_id: Mapped[str] = mapped_column(String(36))
    instruction: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32), default="demo")
    model: Mapped[str] = mapped_column(String(100), default="demo-v1")
    prompt_version: Mapped[str] = mapped_column(String(40), default="revision-v1")
    previous_task_id: Mapped[str | None] = mapped_column(String(36))
    feedback: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    idempotency_key: Mapped[str] = mapped_column(String(80))
    request_hash: Mapped[str] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    claim_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Candidate(Base):
    __tablename__ = "wa_candidates"
    task_id: Mapped[str] = mapped_column(ForeignKey("wa_tasks.id"), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)


class Attempt(Base):
    __tablename__ = "wa_attempts"
    __table_args__ = (UniqueConstraint("task_id", "number", name="wa_attempt_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("wa_tasks.id"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    claim_token: Mapped[str] = mapped_column(String(36), unique=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    usage: Mapped[dict | None] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(20), default="running")
    error_code: Mapped[str | None] = mapped_column(String(80))


class TaskEvent(Base):
    __tablename__ = "wa_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("wa_tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ImportBatch(Base):
    __tablename__ = "wa_imports"
    __table_args__ = (UniqueConstraint("user_id", "source_hash", name="wa_import_once"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("wa_users.id"))
    source_hash: Mapped[str] = mapped_column(String(64))
    mapping: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
