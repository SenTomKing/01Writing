"""DeepSeek 候选生成器。"""

import json
from typing import Any

from ..errors import ConfigurationError, GenerationError, ValidationError
from ..models import RevisionCandidate, RevisionRequest
from ..validation import validate_candidate_payload


SYSTEM_PROMPT = """你是中文写作修改助手。
请遵守用户本轮给出的修改要求，并且不要改变原意或捏造事实。
只返回一个 json 对象，不要返回 Markdown 代码块或额外解释。
json 格式必须严格为：
{"text": "修改后的完整文字", "reason": "简明说明主要修改"}
"""


def build_user_prompt(request: RevisionRequest) -> str:
    """把项目内部的修订请求整理成发送给模型的用户消息。"""

    return (
        "请根据本轮修改要求修改原文。\n\n"
        f"原文：\n{request.source_text}\n\n"
        f"本轮修改要求：\n{request.instruction}"
    )


class DeepSeekCandidateGenerator:
    """调用 DeepSeek，并把响应转换成项目内部的候选对象。"""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_candidate_chars: int,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ConfigurationError("DeepSeek API key 不能为空")
        self._model = model
        self._max_candidate_chars = max_candidate_chars

        if client is not None:
            self._client = client
            return

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ConfigurationError(
                "缺少 openai，请先运行：python -m pip install -e ."
            ) from exc

        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )

    @property
    def model_name(self) -> str:
        return self._model

    def generate(self, request: RevisionRequest) -> RevisionCandidate:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_prompt(request)},
                ],
                response_format={"type": "json_object"},
                max_tokens=2_048,
                temperature=0.3,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except Exception as exc:
            raise GenerationError(f"DeepSeek 请求失败：{exc}") from exc

        try:
            choice = response.choices[0]
            if getattr(choice, "finish_reason", None) == "length":
                raise GenerationError("模型输出因长度限制被截断")
            content = choice.message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise GenerationError("DeepSeek 返回了无法识别的响应结构") from exc

        if not isinstance(content, str) or not content.strip():
            raise GenerationError("DeepSeek 返回了空内容")

        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise GenerationError("DeepSeek 返回的内容不是合法 JSON") from exc

        try:
            return validate_candidate_payload(
                payload,
                max_text_chars=self._max_candidate_chars,
            )
        except ValidationError as exc:
            raise GenerationError(f"DeepSeek 输出格式不符合要求：{exc}") from exc
