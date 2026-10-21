"""命令行菜单与历史展示测试。"""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.cli import (
    display_history,
    make_text_preview,
    read_action,
    read_rejection_reason,
    run_revision_session,
)
from writing_assistant.models import (
    RevisionCandidate,
    RevisionRecord,
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
        round_number = len(self.calls)
        return RevisionCandidate(
            text=f"第 {round_number} 版候选",
            reason=f"第 {round_number} 次测试生成",
        )


class CliHistoryTests(unittest.TestCase):
    def test_read_action_repeats_until_choice_is_valid(self) -> None:
        answers = iter(["其他", "2"])
        output = StringIO()

        with redirect_stdout(output):
            action = read_action(lambda _prompt: next(answers))

        self.assertEqual(action, "2")
        self.assertIn("请输入 1 或 2。", output.getvalue())

    def test_make_text_preview_flattens_and_truncates_text(self) -> None:
        preview = make_text_preview("第一行\n第二行很长", max_chars=6)

        self.assertEqual(preview, "第一行 第二…")

    def test_display_history_handles_empty_records(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            display_history([])

        self.assertIn("暂无修订记录", output.getvalue())

    def test_display_history_prints_saved_record(self) -> None:
        record = RevisionRecord.create_pending(
            original_text="原文",
            instruction="使表达更简洁",
            candidate=RevisionCandidate(text="候选", reason="测试原因"),
            model="fake-model",
        )
        record.accept()
        output = StringIO()

        with redirect_stdout(output):
            display_history([record])

        printed = output.getvalue()
        self.assertIn("共有 1 条修订记录", printed)
        self.assertIn("状态：accepted", printed)
        self.assertIn("原文：原文", printed)
        self.assertIn("修改要求：使表达更简洁", printed)
        self.assertIn("最终正文：候选", printed)

    def test_display_history_marks_old_record_without_instruction(self) -> None:
        record = RevisionRecord.create_pending(
            original_text="旧原文",
            instruction="临时要求",
            candidate=RevisionCandidate(text="旧候选", reason="测试原因"),
            model="fake-model",
        )
        record.instruction = None
        output = StringIO()

        with redirect_stdout(output):
            display_history([record])

        self.assertIn("修改要求：未记录", output.getvalue())

    def test_read_rejection_reason_repeats_after_blank_input(self) -> None:
        answers = iter(["   ", "语气太强了"])
        output = StringIO()

        with redirect_stdout(output):
            reason = read_rejection_reason(lambda _prompt: next(answers))

        self.assertEqual(reason, "语气太强了")
        self.assertIn("拒绝原因不能为空", output.getvalue())

    def test_revision_session_retries_after_rejection_until_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonRevisionStore(Path(directory) / "revisions.json")
            generator = FakeGenerator()
            workflow = RevisionWorkflow(
                generator=generator,
                store=store,
                max_input_chars=100,
            )
            answers = iter(
                [
                    "这个项目太好了啊",
                    "删除口语化表达",
                    "n",
                    "语气太强，只需要删除语气词",
                    "y",
                ]
            )
            output = StringIO()

            with redirect_stdout(output):
                final_record = run_revision_session(
                    workflow,
                    lambda _prompt: next(answers),
                )

            records = store.list_all()

        self.assertEqual(len(generator.calls), 2)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].status, RevisionStatus.REJECTED)
        self.assertEqual(
            records[0].rejection_reason,
            "语气太强，只需要删除语气词",
        )
        self.assertEqual(records[1].status, RevisionStatus.ACCEPTED)
        self.assertEqual(records[1].previous_record_id, records[0].id)
        self.assertEqual(len(generator.calls[1].feedback_history), 1)
        self.assertEqual(final_record.id, records[1].id)
        self.assertIn("将根据反馈重新生成", output.getvalue())


if __name__ == "__main__":
    unittest.main()
