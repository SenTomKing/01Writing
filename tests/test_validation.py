"""校验模块测试。"""

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.errors import ValidationError
from writing_assistant.models import RevisionCandidate
from writing_assistant.validation import (
    validate_candidate,
    validate_candidate_payload,
    validate_input,
)


class ValidateInputTests(unittest.TestCase):
    def test_normal_text_is_trimmed(self) -> None:
        self.assertEqual(validate_input("  一段文字  "), "一段文字")

    def test_blank_text_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "不能为空"):
            validate_input(" \n\t ")

    def test_non_string_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "必须是字符串"):
            validate_input(123)

    def test_overlong_text_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "不能超过"):
            validate_input("超过上限", max_chars=3)


class ValidateCandidateTests(unittest.TestCase):
    def test_valid_payload_becomes_candidate(self) -> None:
        candidate = validate_candidate_payload(
            {"text": "  修改后  ", "reason": "  更清楚  "},
            max_text_chars=100,
        )
        self.assertEqual(candidate.text, "修改后")
        self.assertEqual(candidate.reason, "更清楚")

    def test_missing_reason_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "reason"):
            validate_candidate_payload({"text": "修改后"}, max_text_chars=100)

    def test_candidate_object_is_checked_again(self) -> None:
        candidate = RevisionCandidate(text="修改后", reason="原因")
        self.assertEqual(
            validate_candidate(candidate, max_text_chars=100),
            candidate,
        )


if __name__ == "__main__":
    unittest.main()
