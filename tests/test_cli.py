"""命令行菜单与历史展示测试。"""

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.cli import display_history, make_text_preview, read_action
from writing_assistant.models import RevisionCandidate, RevisionRecord


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


if __name__ == "__main__":
    unittest.main()
