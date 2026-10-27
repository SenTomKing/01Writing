"""资料来源、索引与有界导入作业。"""
from alembic import op
import sqlalchemy as sa

revision = "v3_research"
down_revision = "v2_agent"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("wa_knowledge", sa.Column("provenance",sa.JSON(),nullable=False,server_default="{}"))
    op.add_column("wa_knowledge", sa.Column("chunk_index",sa.JSON(),nullable=False,server_default="{}"))
    op.create_table("wa_research_jobs",
        sa.Column("id",sa.String(36),primary_key=True),
        sa.Column("user_id",sa.String(36),sa.ForeignKey("wa_users.id"),nullable=False),
        sa.Column("kind",sa.String(16),nullable=False),sa.Column("title",sa.String(200),nullable=False),
        sa.Column("payload",sa.JSON(),nullable=False),sa.Column("status",sa.String(16),nullable=False),
        sa.Column("progress",sa.String(200),nullable=False),sa.Column("error",sa.String(300)),
        sa.Column("knowledge_id",sa.String(36)),sa.Column("claim_token",sa.String(36)),
        sa.Column("lease_until",sa.DateTime(timezone=True)),sa.Column("attempts",sa.Integer(),nullable=False),
        sa.Column("dispatch_after",sa.DateTime(timezone=True),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("finished_at",sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','cancelled')",name="wa_research_status"))
    op.create_index("wa_research_dispatch","wa_research_jobs",["status","dispatch_after"])
    op.create_index("ix_wa_research_jobs_user_id","wa_research_jobs",["user_id"])


def downgrade():
    op.drop_table("wa_research_jobs")
    op.drop_column("wa_knowledge","chunk_index")
    op.drop_column("wa_knowledge","provenance")
