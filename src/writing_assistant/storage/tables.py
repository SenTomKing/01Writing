"""文档及正文快照的关系数据库结构。"""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DocumentRow(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("length(trim(title)) BETWEEN 1 AND 200", name="ck_documents_title"),
        # 联合外键不仅要求版本存在，还要求它属于当前文档。
        ForeignKeyConstraint(
            ["id", "current_version_id"],
            ["text_versions.document_id", "text_versions.id"],
            name="fk_documents_current_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_documents_created_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    current_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False)


class TextVersionRow(Base):
    __tablename__ = "text_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_versions_document",
            deferrable=True, initially="DEFERRED",
        ),
        UniqueConstraint("document_id", "version_number", name="uq_versions_document_number"),
        UniqueConstraint("document_id", "id", name="uq_versions_document_id"),
        CheckConstraint("version_number > 0", name="ck_versions_positive_number"),
        CheckConstraint("length(trim(content)) > 0", name="ck_versions_content"),
        CheckConstraint(
            "source_revision_id IS NULL OR restored_from_version_id IS NULL",
            name="ck_versions_single_source",
        ),
        ForeignKeyConstraint(
            ["document_id", "restored_from_version_id"],
            ["text_versions.document_id", "text_versions.id"],
            name="fk_versions_restore_source", deferrable=True, initially="DEFERRED",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    source_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    restored_from_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
