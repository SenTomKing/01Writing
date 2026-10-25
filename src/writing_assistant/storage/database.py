"""建立 SQLite 连接、独立会话与版本化结构迁移。"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import sessionmaker


class Database:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path.resolve()
        self.engine = create_engine(
            URL.create("sqlite+pysqlite", database=str(self.file_path)),
            connect_args={"check_same_thread": False, "timeout": 5},
            hide_parameters=True,
        )

        @event.listens_for(self.engine, "connect")
        def configure_sqlite(connection, _connection_record) -> None:
            # 显式 BEGIN，保证 SELECT 和结构迁移也遵守事务边界。
            connection.isolation_level = None
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()

        @event.listens_for(self.engine, "begin")
        def begin_transaction(connection) -> None:
            connection.exec_driver_sql("BEGIN")

        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def initialize(self) -> None:
        """本地服务启动时应用缺失的迁移，不覆盖已有数据。"""

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        config = Config()
        script_location = str(Path(__file__).with_name("migrations"))
        config.set_main_option("script_location", script_location.replace("%", "%%"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def dispose(self) -> None:
        self.engine.dispose()
