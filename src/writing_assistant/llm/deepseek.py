"""DeepSeek 候选生成器。"""

import json
from typing import Any

from ..errors import ConfigurationError, GenerationError, ValidationError
from ..models import RevisionCandidate
from ..validation import validate_candidate_payload


SYSTEM_PROMPT = """你是中文写作修改助手。
请在不改变原意、不捏造事实的前提下，优化用户文字的准确性、清晰度和流畅度。
只返回一个 json 对象，不要返回 Markdown 代码块或额外解释。
json 格式必须严格为：
{"text": "修改后的完整文字", "reason": "简明说明主要修改"}
"""


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

    def generate(self, original_text: str) -> RevisionCandidate:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": original_text},
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
