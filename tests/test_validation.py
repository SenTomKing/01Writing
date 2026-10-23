"""校验模块测试。"""

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.errors import ValidationError
from writing_assistant.models import RevisionCandidate, RevisionFeedback
from writing_assistant.validation import (
    validate_candidate,
    validate_candidate_payload,
    validate_document_content,
    validate_document_title,
    validate_feedback,
    validate_input,
    validate_instruction,
    validate_rejection_reason,
)


class ValidateDocumentTests(unittest.TestCase):
    def test_title_and_content_are_trimmed(self) -> None:
        self.assertEqual(validate_document_title("  项目介绍  "), "项目介绍")
        self.assertEqual(
            validate_document_content("  第一版正文  "),
            "第一版正文",
        )

    def test_blank_title_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "文档标题不能为空"):
            validate_document_title("   ")

    def test_overlong_content_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "文档正文不能超过"):
            validate_document_content("四个字符", max_chars=3)


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


class ValidateInstructionTests(unittest.TestCase):
    def test_normal_instruction_is_trimmed(self) -> None:
        self.assertEqual(
            validate_instruction("  使表达更简洁  "),
            "使表达更简洁",
        )

    def test_blank_instruction_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "修改要求不能为空"):
            validate_instruction(" \n\t ")

    def test_overlong_instruction_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "修改要求不能超过"):
            validate_instruction("写得更简洁", max_chars=3)


class ValidateRejectionReasonTests(unittest.TestCase):
    def test_normal_reason_is_trimmed(self) -> None:
        self.assertEqual(
            validate_rejection_reason("  语气太强了  "),
            "语气太强了",
        )

    def test_blank_reason_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "拒绝原因不能为空"):
            validate_rejection_reason("   ")

    def test_feedback_normalizes_user_reason(self) -> None:
        feedback = RevisionFeedback(
            rejected_candidate=RevisionCandidate(text="候选", reason="说明"),
            user_feedback="  仍然太口语化  ",
        )

        normalized = validate_feedback(feedback, max_candidate_chars=100)

        self.assertEqual(normalized.user_feedback, "仍然太口语化")


if __name__ == "__main__":
    unittest.main()
