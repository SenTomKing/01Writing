"""输入和模型候选的校验规则。"""

from typing import Any

from .errors import ValidationError
from .models import RevisionCandidate


DEFAULT_MAX_INPUT_CHARS = 10_000
MAX_REASON_CHARS = 2_000


def validate_input(
    text: Any,
    *,
    max_chars: int = DEFAULT_MAX_INPUT_CHARS,
) -> str:
    """校验并规范化用户输入，成功时返回去除首尾空白的文字。"""

    if not isinstance(text, str):
        raise ValidationError("输入必须是字符串")

    normalized = text.strip()
    if not normalized:
        raise ValidationError("输入不能为空或只包含空白字符")
    if len(normalized) > max_chars:
        raise ValidationError(
            f"输入不能超过 {max_chars} 个字符，当前为 {len(normalized)} 个字符"
        )
    return normalized


def validate_candidate_payload(
    payload: Any,
    *,
    max_text_chars: int,
) -> RevisionCandidate:
    """把模型返回的未知数据校验并转换为候选对象。"""

    if not isinstance(payload, dict):
        raise ValidationError("模型输出必须是 JSON 对象")

    text = payload.get("text")
    reason = payload.get("reason")
    if not isinstance(text, str):
        raise ValidationError("模型输出的 text 必须是字符串")
    if not isinstance(reason, str):
        raise ValidationError("模型输出的 reason 必须是字符串")

    normalized_text = text.strip()
    normalized_reason = reason.strip()
    if not normalized_text:
        raise ValidationError("模型输出的 text 不能为空")
    if not normalized_reason:
        raise ValidationError("模型输出的 reason 不能为空")
    if len(normalized_text) > max_text_chars:
        raise ValidationError(
            f"模型输出的 text 不能超过 {max_text_chars} 个字符"
        )
    if len(normalized_reason) > MAX_REASON_CHARS:
        raise ValidationError(
            f"模型输出的 reason 不能超过 {MAX_REASON_CHARS} 个字符"
        )

    return RevisionCandidate(text=normalized_text, reason=normalized_reason)


def validate_candidate(
    candidate: RevisionCandidate,
    *,
    max_text_chars: int,
) -> RevisionCandidate:
    """在模块边界再次校验候选，防止其他生成器绕过规则。"""

    if not isinstance(candidate, RevisionCandidate):
        raise ValidationError("候选必须是 RevisionCandidate")
    return validate_candidate_payload(
        candidate.to_dict(),
        max_text_chars=max_text_chars,
    )
