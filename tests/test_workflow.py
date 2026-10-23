"""核心工作流测试。"""

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.errors import InvalidDecisionError, ValidationError
from writing_assistant.models import (
    RevisionCandidate,
    RevisionRequest,
    RevisionStatus,
)
from writing_assistant.storage.json_store import JsonRevisionStore
from writing_assistant.workflow import RevisionWorkflow


class FakeGenerator:
    def __init__(self) -> None:
        self.calls: list[RevisionRequest] = []

    @property
    def model_name(self) -> str:
        return "fake-model"

    def generate(self, request: RevisionRequest) -> RevisionCandidate:
        self.calls.append(request)
        return RevisionCandidate(
            text=f"{request.source_text}（修改后）",
            reason="测试修改",
        )


class RevisionWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        file_path = Path(self.temporary_directory.name) / "revisions.json"
        self.store = JsonRevisionStore(file_path)
        self.generator = FakeGenerator()
        self.workflow = RevisionWorkflow(
            generator=self.generator,
            store=self.store,
            max_input_chars=100,
        )

    @staticmethod
    def make_request(
        source_text: str = "原文",
        instruction: str = "使表达更清楚",
    ) -> RevisionRequest:
        return RevisionRequest(
            source_text=source_text,
            instruction=instruction,
        )

    def test_invalid_input_does_not_call_generator(self) -> None:
        with self.assertRaises(ValidationError):
            self.workflow.create_candidate(self.make_request(source_text="   "))
        self.assertEqual(self.generator.calls, [])
        self.assertEqual(self.store.list_all(), [])

    def test_invalid_instruction_does_not_call_generator(self) -> None:
        with self.assertRaisesRegex(ValidationError, "修改要求不能为空"):
            self.workflow.create_candidate(self.make_request(instruction="   "))
        self.assertEqual(self.generator.calls, [])
        self.assertEqual(self.store.list_all(), [])

    def test_candidate_is_saved_as_pending_before_decision(self) -> None:
        record = self.workflow.create_candidate(
            self.make_request(
                source_text="  原文  ",
                instruction="  使表达更清楚  ",
            )
        )
        saved = self.store.get(record.id)
        self.assertEqual(saved.status, RevisionStatus.PENDING)
        self.assertIsNone(saved.final_text)
        self.assertEqual(saved.original_text, "原文")
        self.assertEqual(saved.instruction, "使表达更清楚")
        self.assertEqual(self.generator.calls, [self.make_request()])

    def test_accept_uses_candidate_as_final_text(self) -> None:
        record = self.workflow.create_candidate(self.make_request())
        decided = self.workflow.decide(record.id, accept=True)
        self.assertEqual(decided.status, RevisionStatus.ACCEPTED)
        self.assertEqual(decided.final_text, "原文（修改后）")
        self.assertIsNotNone(decided.decided_at)

    def test_reject_keeps_original_as_final_text(self) -> None:
        record = self.workflow.create_candidate(self.make_request())
        decided = self.workflow.decide(
            record.id,
            accept=False,
            rejection_reason="语气太强了",
        )
        self.assertEqual(decided.status, RevisionStatus.REJECTED)
        self.assertEqual(decided.final_text, "原文")
        self.assertEqual(decided.rejection_reason, "语气太强了")

    def test_reject_requires_reason_and_keeps_record_pending(self) -> None:
        record = self.workflow.create_candidate(self.make_request())

        with self.assertRaisesRegex(ValidationError, "拒绝原因不能为空"):
            self.workflow.decide(record.id, accept=False)

        self.assertEqual(self.store.get(record.id).status, RevisionStatus.PENDING)

    def test_retry_request_contains_saved_feedback(self) -> None:
        first_record = self.workflow.create_candidate(self.make_request())
        rejected_record = self.workflow.decide(
            first_record.id,
            accept=False,
            rejection_reason="  语气太强了  ",
        )

        retry_request = self.workflow.create_retry_request(
            self.make_request(),
            rejected_record.id,
        )
        second_record = self.workflow.create_candidate(
            retry_request,
            previous_record_id=rejected_record.id,
        )

        self.assertEqual(len(retry_request.feedback_history), 1)
        feedback = retry_request.feedback_history[0]
        self.assertEqual(feedback.rejected_candidate, first_record.candidate)
        self.assertEqual(feedback.user_feedback, "语气太强了")
        self.assertEqual(second_record.previous_record_id, rejected_record.id)
        self.assertEqual(self.generator.calls[-1], retry_request)

    def test_retry_link_rejects_mismatched_feedback_history(self) -> None:
        first_record = self.workflow.create_candidate(self.make_request())
        rejected_record = self.workflow.decide(
            first_record.id,
            accept=False,
            rejection_reason="语气太强了",
        )

        with self.assertRaisesRegex(InvalidDecisionError, "反馈历史.*不一致"):
            self.workflow.create_candidate(
                self.make_request(),
                previous_record_id=rejected_record.id,
            )

        self.assertEqual(len(self.generator.calls), 1)

    def test_same_record_cannot_be_decided_twice(self) -> None:
        record = self.workflow.create_candidate(self.make_request())
        self.workflow.decide(record.id, accept=True)
        with self.assertRaises(InvalidDecisionError):
            self.workflow.decide(
                record.id,
                accept=False,
                rejection_reason="不合适",
            )

    def test_list_history_returns_saved_records_in_storage_order(self) -> None:
        first_record = self.workflow.create_candidate(
            self.make_request(source_text="第一段原文")
        )
        self.workflow.decide(first_record.id, accept=True)
        second_record = self.workflow.create_candidate(
            self.make_request(source_text="第二段原文")
        )
        self.workflow.decide(
            second_record.id,
            accept=False,
            rejection_reason="不合适",
        )

        records = self.workflow.list_history()

        self.assertEqual(
            [record.id for record in records],
            [first_record.id, second_record.id],
        )


if __name__ == "__main__":
    unittest.main()
