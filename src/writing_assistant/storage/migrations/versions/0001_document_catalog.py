"""建立文档与正文版本表。"""

from alembic import op
import sqlalchemy as sa


revision = "0001_document_catalog"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("current_version_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.CheckConstraint("length(trim(title)) BETWEEN 1 AND 200", name="ck_documents_title"),
        sa.ForeignKeyConstraint(
            ["id", "current_version_id"], ["text_versions.document_id", "text_versions.id"],
            name="fk_documents_current_version", use_alter=True,
            deferrable=True, initially="DEFERRED",
        ),
    )
    op.create_table(
        "text_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("source_revision_id", sa.String(36), nullable=True),
        sa.Column("restored_from_version_id", sa.String(36), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name="fk_versions_document",
            deferrable=True, initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "restored_from_version_id"],
            ["text_versions.document_id", "text_versions.id"],
            name="fk_versions_restore_source", deferrable=True, initially="DEFERRED",
        ),
        sa.UniqueConstraint("document_id", "version_number", name="uq_versions_document_number"),
        sa.UniqueConstraint("document_id", "id", name="uq_versions_document_id"),
        sa.CheckConstraint("version_number > 0", name="ck_versions_positive_number"),
        sa.CheckConstraint("length(trim(content)) > 0", name="ck_versions_content"),
        sa.CheckConstraint(
            "source_revision_id IS NULL OR restored_from_version_id IS NULL",
            name="ck_versions_single_source",
        ),
    )
    op.create_index("ix_documents_created_id", "documents", ["created_at", "id"])


def downgrade() -> None:
    # 初始结构存在相互引用；拒绝隐式删表，避免把回退误当作无损操作。
    raise RuntimeError("初始结构不支持自动删表回退；请保留数据库备份")
