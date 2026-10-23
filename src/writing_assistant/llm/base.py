"""候选生成器需要遵守的最小接口。"""

from typing import Protocol

from ..models import RevisionCandidate, RevisionRequest
from ..errors import ConfigurationError


class CandidateGenerator(Protocol):
    """任何候选生成器只要满足这两个成员，就能接入工作流。"""

    @property
    def model_name(self) -> str: ...

    def generate(self, request: RevisionRequest) -> RevisionCandidate: ...


class UnavailableCandidateGenerator:
    """无密钥时允许确认已保存候选，只有需要生成才报配置错误。"""

    @property
    def model_name(self) -> str:
        return "尚未配置"

    def generate(self, request: RevisionRequest) -> RevisionCandidate:
        raise ConfigurationError("继续生成前请填写 DEEPSEEK_API_KEY；已保存的任务仍可恢复")
