"""输入和模型候选的校验规则。"""

from typing import Any

from .errors import ValidationError
from .models import RevisionCandidate, RevisionFeedback


DEFAULT_MAX_INPUT_CHARS = 10_000
DEFAULT_MAX_TITLE_CHARS = 200
DEFAULT_MAX_INSTRUCTION_CHARS = 2_000
DEFAULT_MAX_FEEDBACK_CHARS = 2_000
MAX_REASON_CHARS = 2_000


def validate_document_title(
    title: Any,
    *,
    max_chars: int = DEFAULT_MAX_TITLE_CHARS,
) -> str:
    """校验文档标题，成功时返回去除首尾空白的标题。"""

    if not isinstance(title, str):
        raise ValidationError("文档标题必须是字符串")

    normalized = title.strip()
    if not normalized:
        raise ValidationError("文档标题不能为空或只包含空白字符")
    if len(normalized) > max_chars:
        raise ValidationError(
            f"文档标题不能超过 {max_chars} 个字符，当前为 {len(normalized)} 个字符"
        )
    return normalized


def validate_document_content(
    content: Any,
    *,
    max_chars: int = DEFAULT_MAX_INPUT_CHARS,
) -> str:
    """校验文档正文，成功时返回去除首尾空白的正文。"""

    if not isinstance(content, str):
        raise ValidationError("文档正文必须是字符串")

    normalized = content.strip()
    if not normalized:
        raise ValidationError("文档正文不能为空或只包含空白字符")
    if len(normalized) > max_chars:
        raise ValidationError(
            f"文档正文不能超过 {max_chars} 个字符，当前为 {len(normalized)} 个字符"
        )
    return normalized


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


def validate_instruction(
    instruction: Any,
    *,
    max_chars: int = DEFAULT_MAX_INSTRUCTION_CHARS,
) -> str:
    """校验并规范化本轮修改要求。"""

    if not isinstance(instruction, str):
        raise ValidationError("修改要求必须是字符串")

    normalized = instruction.strip()
    if not normalized:
        raise ValidationError("修改要求不能为空或只包含空白字符")
    if len(normalized) > max_chars:
        raise ValidationError(
            f"修改要求不能超过 {max_chars} 个字符，当前为 {len(normalized)} 个字符"
        )
    return normalized


def validate_rejection_reason(
    reason: Any,
    *,
    max_chars: int = DEFAULT_MAX_FEEDBACK_CHARS,
) -> str:
    """校验并规范化使用者拒绝候选时给出的原因。"""

    if not isinstance(reason, str):
        raise ValidationError("拒绝原因必须是字符串")

    normalized = reason.strip()
    if not normalized:
        raise ValidationError("拒绝原因不能为空或只包含空白字符")
    if len(normalized) > max_chars:
        raise ValidationError(
            f"拒绝原因不能超过 {max_chars} 个字符，当前为 {len(normalized)} 个字符"
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


def validate_feedback(
    feedback: RevisionFeedback,
    *,
    max_candidate_chars: int,
) -> RevisionFeedback:
    """校验一轮反馈，并返回内容已经规范化的新对象。"""

    if not isinstance(feedback, RevisionFeedback):
        raise ValidationError("反馈必须是 RevisionFeedback")

    return RevisionFeedback(
        rejected_candidate=validate_candidate(
            feedback.rejected_candidate,
            max_text_chars=max_candidate_chars,
        ),
        user_feedback=validate_rejection_reason(feedback.user_feedback),
    )
