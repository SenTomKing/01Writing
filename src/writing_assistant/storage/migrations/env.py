"""使用应用传入的连接执行迁移，共享其事务。"""

from alembic import context

from writing_assistant.storage.tables import Base


connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("请通过 Database.initialize 提供迁移连接")

context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
with context.begin_transaction():
    context.run_migrations()
