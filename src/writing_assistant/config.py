"""从环境变量加载运行配置。"""

from dataclasses import dataclass
import os
from pathlib import Path

from .errors import ConfigurationError


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"


@dataclass(frozen=True, slots=True)
class AppConfig:
    api_key: str
    base_url: str
    model: str
    data_file: Path
    max_input_chars: int
    request_timeout_seconds: float


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_config(*, project_root: Path | None = None) -> AppConfig:
    """先读取项目根目录的 .env，再读取并校验所需配置。"""

    root = (project_root or get_project_root()).resolve()
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise ConfigurationError(
            "缺少 python-dotenv，请先运行：python -m pip install -e ."
        ) from exc

    load_dotenv(root / ".env")

    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise ConfigurationError(
            "没有找到 DEEPSEEK_API_KEY，请复制 .env.example 为 .env 并填写密钥"
        )

    base_url = os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip()
    model = os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL).strip()
    if not base_url or not model:
        raise ConfigurationError("DEEPSEEK_BASE_URL 和 DEEPSEEK_MODEL 不能为空")

    max_input_chars = _read_positive_int(
        "WRITING_MAX_INPUT_CHARS",
        default=10_000,
    )
    timeout = _read_positive_float(
        "DEEPSEEK_TIMEOUT_SECONDS",
        default=60.0,
    )

    data_value = os.getenv("WRITING_DATA_FILE", "data/revisions.json").strip()
    if not data_value:
        raise ConfigurationError("WRITING_DATA_FILE 不能为空")
    data_file = Path(data_value)
    if not data_file.is_absolute():
        data_file = root / data_file

    return AppConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        data_file=data_file.resolve(),
        max_input_chars=max_input_chars,
        request_timeout_seconds=timeout,
    )


def _read_positive_int(name: str, *, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是整数") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} 必须大于 0")
    return value


def _read_positive_float(name: str, *, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必须是数字") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} 必须大于 0")
    return value
