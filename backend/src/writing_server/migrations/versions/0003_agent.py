"""Agent 资料、显式记忆及领取令牌保护的步骤快照。"""

from alembic import op
import sqlalchemy as sa

revision = "v2_agent"
down_revision = "v1_sources"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("wa_tasks", sa.Column("strategy", sa.String(20), nullable=False, server_default="workflow"))
    op.add_column("wa_tasks", sa.Column("context", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("wa_tasks", sa.Column("checkpoint", sa.JSON(), nullable=False, server_default="{}"))
    op.create_table(
        "wa_knowledge",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("wa_users.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('source','memory')", name="wa_knowledge_kind"),
    )
    op.create_index("ix_wa_knowledge_user_id", "wa_knowledge", ["user_id"])


def downgrade():
    op.drop_table("wa_knowledge")
    for column in ("checkpoint", "context", "strategy"):
        op.drop_column("wa_tasks", column)
