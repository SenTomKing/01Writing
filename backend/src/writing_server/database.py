"""每次请求／任务使用独立会话；数据库结构只由迁移命令修改。"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .settings import Settings


class Database:
    def __init__(self, settings: Settings, *, migration=False):
        if migration and settings.migration_url is None:
            raise ValueError("迁移需要独立的 WRITING_DB_MIGRATION_PASSWORD 或 WRITING_MIGRATION_URL")
        self.engine = create_engine(
            settings.migration_url if migration else settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=5,
            hide_parameters=True,
            connect_args={"connect_timeout": 5},
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self):
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
        with self.engine.begin() as connection:
            # 同一个库只允许一个迁移进程。
            from sqlalchemy import text

            connection.execute(text("SELECT pg_advisory_xact_lock(17010001)"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def close(self):
        self.engine.dispose()
