"""JSON 存储测试。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.errors import RecordNotFoundError, StorageError
from writing_assistant.models import RevisionCandidate, RevisionRecord
from writing_assistant.storage.json_store import JsonRevisionStore


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
        record_data.pop("instruction")
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


if __name__ == "__main__":
    unittest.main()
