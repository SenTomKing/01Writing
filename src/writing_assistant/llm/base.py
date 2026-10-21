"""候选生成器需要遵守的最小接口。"""

from typing import Protocol

from ..models import RevisionCandidate, RevisionRequest


class CandidateGenerator(Protocol):
    """任何候选生成器只要满足这两个成员，就能接入工作流。"""

    @property
    def model_name(self) -> str: ...

    def generate(self, request: RevisionRequest) -> RevisionCandidate: ...
