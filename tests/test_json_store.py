"""JSON 存储测试。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.errors import RecordNotFoundError, StorageError
from writing_assistant.models import Document, RevisionCandidate, RevisionRecord
from writing_assistant.storage.json_store import JsonProjectStore, JsonRevisionStore


class JsonRevisionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.file_path = Path(self.temporary_directory.name) / "data" / "revisions.json"
        self.store = JsonRevisionStore(self.file_path)

    @staticmethod
    def make_record() -> RevisionRecord:
        return RevisionRecord.create_pending(
            original_text="原文",
            instruction="使表达更简洁",
            candidate=RevisionCandidate(text="候选", reason="原因"),
            model="fake-model",
        )

    def test_add_creates_parent_and_round_trips_record(self) -> None:
        record = self.make_record()
        self.store.add(record)
        self.assertTrue(self.file_path.exists())
        self.assertEqual(self.store.get(record.id).to_dict(), record.to_dict())

    def test_missing_record_raises_clear_error(self) -> None:
        with self.assertRaises(RecordNotFoundError):
            self.store.get("missing")

    def test_broken_json_is_not_overwritten(self) -> None:
        self.file_path.parent.mkdir(parents=True)
        broken_content = "{broken json"
        self.file_path.write_text(broken_content, encoding="utf-8")

        with self.assertRaisesRegex(StorageError, "停止写入"):
            self.store.add(self.make_record())

        self.assertEqual(
            self.file_path.read_text(encoding="utf-8"),
            broken_content,
        )

    def test_old_record_without_instruction_can_still_be_read(self) -> None:
        record_data = self.make_record().to_dict()
        for field_name in (
            "instruction",
            "rejection_reason",
            "previous_record_id",
        ):
            record_data.pop(field_name)
        self.file_path.parent.mkdir(parents=True)
        self.file_path.write_text(
            json.dumps(
                {"schema_version": 1, "revisions": [record_data]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        loaded_record = self.store.list_all()[0]

        self.assertIsNone(loaded_record.instruction)
        self.assertIsNone(loaded_record.rejection_reason)
        self.assertIsNone(loaded_record.previous_record_id)

    def test_rejected_retry_record_round_trips_feedback_fields(self) -> None:
        first_record = self.make_record()
        first_record.reject("语气太强了")
        self.store.add(first_record)
        retry_record = RevisionRecord.create_pending(
            original_text="原文",
            instruction="使表达更简洁",
            candidate=RevisionCandidate(text="第二版候选", reason="根据反馈调整"),
            model="fake-model",
            previous_record_id=first_record.id,
        )
        self.store.add(retry_record)

        loaded_first = self.store.get(first_record.id)
        loaded_retry = self.store.get(retry_record.id)

        self.assertEqual(loaded_first.rejection_reason, "语气太强了")
        self.assertEqual(loaded_retry.previous_record_id, first_record.id)


class JsonProjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.file_path = Path(self.temporary_directory.name) / "data" / "project.json"
        self.store = JsonProjectStore(self.file_path)

    @staticmethod
    def make_legacy_record() -> RevisionRecord:
        return RevisionRecord.create_pending(
            original_text="旧原文",
            instruction="旧修改要求",
            candidate=RevisionCandidate(text="旧候选", reason="旧原因"),
            model="fake-model",
        )

    def write_schema_one_file(self) -> str:
        original_content = json.dumps(
            {
                "schema_version": 1,
                "revisions": [self.make_legacy_record().to_dict()],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        self.file_path.parent.mkdir(parents=True)
        self.file_path.write_text(original_content, encoding="utf-8")
        return original_content

    def test_new_document_is_saved_with_schema_two(self) -> None:
        document, version = Document.create_with_initial_version(
            title="项目介绍",
            initial_content="第一版正文",
        )

        self.store.add_document(document, version)

        raw_data = json.loads(self.file_path.read_text(encoding="utf-8"))
        self.assertEqual(raw_data["schema_version"], 2)
        self.assertEqual(raw_data["documents"][0]["title"], "项目介绍")
        self.assertEqual(raw_data["text_versions"][0]["content"], "第一版正文")
        self.assertEqual(self.store.get_document(document.id), document)
        self.assertEqual(self.store.get_text_version(version.id), version)

    def test_first_write_migrates_schema_one_after_creating_backup(self) -> None:
        original_content = self.write_schema_one_file()
        document, version = Document.create_with_initial_version(
            title="新文档",
            initial_content="新正文",
        )

        self.store.add_document(document, version)

        self.assertTrue(self.store.legacy_backup_path.exists())
        self.assertEqual(
            self.store.legacy_backup_path.read_text(encoding="utf-8"),
            original_content,
        )
        migrated_data = json.loads(self.file_path.read_text(encoding="utf-8"))
        self.assertEqual(migrated_data["schema_version"], 2)
        self.assertEqual(len(migrated_data["revisions"]), 1)
        self.assertEqual(len(migrated_data["documents"]), 1)
        self.assertEqual(len(migrated_data["text_versions"]), 1)

    def test_reading_schema_one_does_not_modify_or_backup_file(self) -> None:
        original_content = self.write_schema_one_file()

        records = self.store.list_all()
        documents = self.store.list_documents()

        self.assertEqual(len(records), 1)
        self.assertEqual(documents, [])
        self.assertEqual(
            self.file_path.read_text(encoding="utf-8"),
            original_content,
        )
        self.assertFalse(self.store.legacy_backup_path.exists())

    def test_broken_document_version_relationship_stops_reading(self) -> None:
        document, version = Document.create_with_initial_version(
            title="项目介绍",
            initial_content="第一版正文",
        )
        raw_data = {
            "schema_version": 2,
            "documents": [document.to_dict()],
            "text_versions": [],
            "revisions": [],
        }
        self.file_path.parent.mkdir(parents=True)
        self.file_path.write_text(
            json.dumps(raw_data, ensure_ascii=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(StorageError, "结构不正确"):
            self.store.list_documents()

        self.assertFalse(self.store.legacy_backup_path.exists())

    def test_blank_document_title_in_schema_two_stops_reading(self) -> None:
        document, version = Document.create_with_initial_version(
            title="项目介绍",
            initial_content="第一版正文",
        )
        document_data = document.to_dict()
        document_data["title"] = "   "
        raw_data = {
            "schema_version": 2,
            "documents": [document_data],
            "text_versions": [version.to_dict()],
            "revisions": [],
        }
        self.file_path.parent.mkdir(parents=True)
        self.file_path.write_text(
            json.dumps(raw_data, ensure_ascii=False),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(StorageError, "结构不正确"):
            self.store.list_documents()


if __name__ == "__main__":
    unittest.main()
