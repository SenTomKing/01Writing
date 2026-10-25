"""运行配置：凭证仅留在服务端，开发与容器使用同一套配置名。"""

from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL


def project_root() -> Path:
    configured = os.getenv("WRITING_PROJECT_ROOT")
    if configured:
        return Path(configured).resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


@dataclass(frozen=True)
class Settings:
    database_url: str | URL = field(repr=False)
    migration_url: str | URL | None = field(repr=False)
    redis_url: str = field(default="redis://localhost:6379/0", repr=False)
    provider: str = "demo"
    model: str = "deepseek-flash"
    api_key: str = field(default="", repr=False)
    base_url: str = "https://api.deepseek.com"
    origins: tuple[str, ...] = (
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8000",
    )
    secure_cookie: bool = False
    session_hours: int = 24
    model_timeout: int = 60
    max_attempts: int = 3
    lease_seconds: int = 180
    max_active_tasks: int = 2
    daily_tasks: int = 100
    static_dir: Path | None = None

    @classmethod
    def load(cls) -> "Settings":
        root = project_root()
        # 数据库专用文件先读，避免通用示例中的空密码遮住本机有效配置；进程环境始终优先。
        load_dotenv(root / ".env.postgres.local", override=False)
        load_dotenv(root / ".env", override=False)

        def url(migration=False):
            explicit = os.getenv("WRITING_MIGRATION_URL" if migration else "WRITING_DATABASE_URL")
            if explicit:
                return explicit
            prefix = "WRITING_DB_MIGRATION_" if migration else "WRITING_DB_"
            password = os.getenv(prefix + "PASSWORD", "")
            if not password:
                if migration:
                    return None
                raise ValueError("缺少项目数据库密码，请配置 .env.postgres.local 或数据库 URL")
            return URL.create(
                "postgresql+psycopg",
                username=os.getenv(prefix + "USER", "writing_owner" if migration else "writing_app"),
                password=password,
                host=os.getenv("WRITING_DB_HOST", "127.0.0.1"),
                port=int(os.getenv("WRITING_DB_PORT", "5432")),
                database=os.getenv("WRITING_DB_NAME", "writing"),
            )

        timeout = int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "60"))
        if not 1 <= timeout <= 120:
            raise ValueError("模型超时必须在 1 至 120 秒之间")
        provider = os.getenv("WRITING_PROVIDER", "demo")
        if provider not in {"demo", "deepseek"}:
            raise ValueError("WRITING_PROVIDER 必须为 demo 或 deepseek")
        return cls(
            database_url=url(),
            migration_url=url(True),
            redis_url=os.getenv("WRITING_REDIS_URL", "redis://localhost:6379/0"),
            provider=provider,
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            api_key=os.getenv("DEEPSEEK_API_KEY", ""),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model_timeout=timeout,
            lease_seconds=max(180, timeout * 3 + 30),
            origins=tuple(
                value.strip().rstrip("/")
                for value in os.getenv(
                    "WRITING_ORIGINS",
                    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8000",
                ).split(",")
                if value.strip()
            ),
            secure_cookie=os.getenv("WRITING_SECURE_COOKIE", "false").lower() == "true",
            static_dir=Path(os.getenv("WRITING_STATIC_DIR", str(root / "frontend/dist"))),
        )
