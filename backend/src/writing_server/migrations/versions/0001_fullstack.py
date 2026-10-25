"""v1 初始结构；DDL 快照随本迁移固定，不读取后来改动的 ORM 元数据。"""

from pathlib import Path
from alembic import op

revision = "v1_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    sql = Path(__file__).with_name("0001_schema.sql").read_text(encoding="utf-8")
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade():
    raise RuntimeError("初始库不提供自动清空式回退；请恢复已验证的备份。")
