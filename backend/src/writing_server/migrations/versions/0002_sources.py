"""补强正文版本来源与反馈链的同文档关联。"""

from alembic import op

revision = "v1_sources"
down_revision = "v1_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.create_foreign_key(
        "wa_version_source_task_fk",
        "wa_versions",
        "wa_tasks",
        ["document_id", "source_task_id"],
        ["document_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.drop_constraint("wa_task_previous_fk", "wa_tasks", type_="foreignkey")
    op.create_foreign_key(
        "wa_task_previous_fk",
        "wa_tasks",
        "wa_tasks",
        ["document_id", "previous_task_id"],
        ["document_id", "id"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade():
    op.drop_constraint("wa_version_source_task_fk", "wa_versions", type_="foreignkey")
    op.drop_constraint("wa_task_previous_fk", "wa_tasks", type_="foreignkey")
    op.create_foreign_key(
        "wa_task_previous_fk", "wa_tasks", "wa_tasks", ["document_id", "previous_task_id"], ["document_id", "id"]
    )
