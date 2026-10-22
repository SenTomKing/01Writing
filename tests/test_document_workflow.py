"""文档创建、列表和重新打开流程测试。"""

from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.document_workflow import DocumentWorkflow
from writing_assistant.errors import DocumentNotFoundError, ValidationError
from writing_assistant.storage.json_store import JsonProjectStore


class DocumentWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        file_path = Path(self.temporary_directory.name) / "project.json"
        self.store = JsonProjectStore(file_path)
        self.workflow = DocumentWorkflow(
            store=self.store,
            max_content_chars=100,
        )

    def test_create_document_normalizes_and_persists_both_objects(self) -> None:
        document, version = self.workflow.create_document(
            title="  项目介绍  ",
            initial_content="  这是第一版正文。  ",
        )

        reopened_document, reopened_version = self.workflow.open_document(
            document.id
        )
        self.assertEqual(reopened_document.title, "项目介绍")
        self.assertEqual(reopened_version.content, "这是第一版正文。")
        self.assertEqual(reopened_document.current_version_id, reopened_version.id)
        self.assertEqual(version.id, reopened_version.id)

    def test_blank_title_does_not_create_document(self) -> None:
        with self.assertRaisesRegex(ValidationError, "文档标题不能为空"):
            self.workflow.create_document(
                title="   ",
                initial_content="正文",
            )

        self.assertEqual(self.workflow.list_documents(), [])

    def test_blank_content_does_not_create_document(self) -> None:
        with self.assertRaisesRegex(ValidationError, "文档正文不能为空"):
            self.workflow.create_document(
                title="项目介绍",
                initial_content="   ",
            )

        self.assertEqual(self.workflow.list_documents(), [])

    def test_list_documents_keeps_creation_order(self) -> None:
        first, _ = self.workflow.create_document(
            title="第一篇",
            initial_content="第一篇正文",
        )
        second, _ = self.workflow.create_document(
            title="第二篇",
            initial_content="第二篇正文",
        )

        documents = self.workflow.list_documents()

        self.assertEqual(
            [document.id for document in documents],
            [first.id, second.id],
        )

    def test_open_missing_document_reports_clear_error(self) -> None:
        with self.assertRaises(DocumentNotFoundError):
            self.workflow.open_document("missing")


if __name__ == "__main__":
    unittest.main()
