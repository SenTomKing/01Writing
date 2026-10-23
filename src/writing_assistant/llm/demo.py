"""本地可复现的演示生成器，不发送网络请求。"""

from ..models import RevisionCandidate, RevisionRequest


class DemoCandidateGenerator:
    @property
    def model_name(self) -> str:
        return "离线演示"

    def generate(self, request: RevisionRequest) -> RevisionCandidate:
        number = len(request.feedback_history) + 1
        return RevisionCandidate(
            text=f"{request.source_text}（离线候选{number}）",
            reason="演示仅追加候选编号，用于检查反馈、版本和恢复流程，不代表真实改写质量。",
        )
