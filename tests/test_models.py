"""文档和正文版本数据模型测试。"""

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.models import Document, TextVersion


class DocumentModelTests(unittest.TestCase):
    def test_create_document_also_creates_linked_initial_version(self) -> None:
        document, version = Document.create_with_initial_version(
            title="项目介绍",
            initial_content="这是第一版正文。",
        )

        self.assertEqual(document.current_version_id, version.id)
        self.assertEqual(version.document_id, document.id)
        self.assertEqual(version.version_number, 1)
        self.assertEqual(version.content, "这是第一版正文。")
        self.assertIsNone(version.source_revision_id)

    def test_document_and_version_round_trip_through_dicts(self) -> None:
        document, version = Document.create_with_initial_version(
            title="项目介绍",
            initial_content="这是第一版正文。",
        )

        loaded_document = Document.from_dict(document.to_dict())
        loaded_version = TextVersion.from_dict(version.to_dict())

        self.assertEqual(loaded_document, document)
        self.assertEqual(loaded_version, version)

    def test_text_version_is_immutable(self) -> None:
        _, version = Document.create_with_initial_version(
            title="项目介绍",
            initial_content="这是第一版正文。",
        )

        with self.assertRaises(FrozenInstanceError):
            version.content = "直接覆盖历史版本"

    def test_non_positive_version_number_is_rejected_when_loading(self) -> None:
        _, version = Document.create_with_initial_version(
            title="项目介绍",
            initial_content="这是第一版正文。",
        )
        data = version.to_dict()
        data["version_number"] = 0

        with self.assertRaisesRegex(ValueError, "必须是正整数"):
            TextVersion.from_dict(data)


if __name__ == "__main__":
    unittest.main()
