"""DeepSeek 响应解析测试，不访问真实网络。"""

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.errors import GenerationError
from writing_assistant.llm.deepseek import DeepSeekCandidateGenerator
from writing_assistant.models import (
    RevisionCandidate,
    RevisionFeedback,
    RevisionRequest,
)


class FakeCompletions:
    def __init__(self, response: object) -> None:
        self.response = response
        self.arguments: dict[str, object] | None = None

    def create(self, **kwargs: object) -> object:
        self.arguments = kwargs
        return self.response


def make_client(content: str | None, finish_reason: str = "stop") -> object:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content),
            )
        ]
    )
    completions = FakeCompletions(response)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        test_completions=completions,
    )


class DeepSeekCandidateGeneratorTests(unittest.TestCase):
    @staticmethod
    def make_request() -> RevisionRequest:
        return RevisionRequest(
            source_text="这个项目太好了啊",
            instruction="删除口语化表达，使语气更简洁",
        )

    def build_generator(self, client: object) -> DeepSeekCandidateGenerator:
        return DeepSeekCandidateGenerator(
            api_key="test-key",
            base_url="https://api.deepseek.com",
            model="test-model",
            timeout_seconds=10,
            max_candidate_chars=100,
            client=client,
        )

    def test_valid_json_becomes_candidate(self) -> None:
        client = make_client('{"text":"修改后","reason":"更清楚"}')
        candidate = self.build_generator(client).generate(self.make_request())
        self.assertEqual(candidate.text, "修改后")
        self.assertEqual(candidate.reason, "更清楚")
        self.assertEqual(
            client.test_completions.arguments["response_format"],
            {"type": "json_object"},
        )
        user_message = client.test_completions.arguments["messages"][1]
        self.assertEqual(user_message["role"], "user")
        self.assertIn("这个项目太好了啊", user_message["content"])
        self.assertIn("删除口语化表达，使语气更简洁", user_message["content"])

    def test_feedback_history_is_included_in_user_message(self) -> None:
        request = RevisionRequest(
            source_text="这个项目太好了啊",
            instruction="删除口语化表达，使语气更简洁",
            feedback_history=(
                RevisionFeedback(
                    rejected_candidate=RevisionCandidate(
                        text="这个项目非常出色。",
                        reason="替换口语化表达",
                    ),
                    user_feedback="语气太强，只需要删除语气词",
                ),
            ),
        )
        client = make_client('{"text":"这个项目太好了","reason":"删除语气词"}')

        self.build_generator(client).generate(request)

        user_content = client.test_completions.arguments["messages"][1]["content"]
        self.assertIn("这个项目非常出色。", user_content)
        self.assertIn("替换口语化表达", user_content)
        self.assertIn("语气太强，只需要删除语气词", user_content)

    def test_invalid_json_is_reported(self) -> None:
        client = make_client("not json")
        with self.assertRaisesRegex(GenerationError, "合法 JSON"):
            self.build_generator(client).generate(self.make_request())

    def test_empty_content_is_reported(self) -> None:
        client = make_client("")
        with self.assertRaisesRegex(GenerationError, "空内容"):
            self.build_generator(client).generate(self.make_request())

    def test_truncated_content_is_reported(self) -> None:
        client = make_client("{}", finish_reason="length")
        with self.assertRaisesRegex(GenerationError, "截断"):
            self.build_generator(client).generate(self.make_request())


if __name__ == "__main__":
    unittest.main()
