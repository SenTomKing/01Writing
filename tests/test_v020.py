"""完整文档修订、恢复任务、版本恢复和写入失败的集成测试。"""

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.cli import main, run_action, run_revision_session
from writing_assistant.config import AppConfig
from writing_assistant.document_workflow import DocumentWorkflow
from writing_assistant.errors import GenerationError, InvalidDecisionError, StorageError, VersionConflictError
from writing_assistant.llm.base import UnavailableCandidateGenerator
from writing_assistant.llm.demo import DemoCandidateGenerator
from writing_assistant.models import RevisionCandidate, RevisionRequest, RevisionStatus
from writing_assistant.storage.json_store import JsonProjectStore
from writing_assistant.workflow import RevisionWorkflow


class RecordingGenerator(DemoCandidateGenerator):
    def __init__(self) -> None:
        self.requests: list[RevisionRequest] = []

    def generate(self, request: RevisionRequest) -> RevisionCandidate:
        self.requests.append(request)
        return super().generate(request)


class CompleteWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.file = self.root / "project.json"
        self.store = JsonProjectStore(self.file)
        self.documents = DocumentWorkflow(store=self.store, max_content_chars=1000)
        self.generator = RecordingGenerator()
        self.workflow = RevisionWorkflow(generator=self.generator, store=self.store, max_input_chars=1000)
        self.document, self.initial = self.documents.create_document(title="项目介绍", initial_content="这个项目很好。")
        self.config = AppConfig(None, "", "演示", self.file, 1000, 60.0)

    def new_candidate(self):
        return self.workflow.create_document_candidate(self.document.id, "删除口语，保持语义")

    def test_accept_atomically_advances_current_and_preserves_initial(self):
        record = self.new_candidate()
        self.workflow.decide(record.id, accept=True)
        reopened = JsonProjectStore(self.file)
        versions = reopened.list_text_versions(self.document.id)
        self.assertEqual([v.version_number for v in versions], [1, 2])
        self.assertEqual(versions[0], self.initial)
        self.assertEqual(versions[1].source_revision_id, record.id)
        self.assertEqual(versions[1].content, record.candidate.text)
        self.assertEqual(reopened.get_document(self.document.id).current_version_id, versions[1].id)
        self.assertEqual(reopened.get(record.id).status, RevisionStatus.ACCEPTED)
        self.assertEqual(reopened.list_unfinished(), [])

    def test_reject_does_not_append_formal_version(self):
        record = self.new_candidate()
        self.workflow.decide(record.id, accept=False, rejection_reason="不要增强语气")
        self.assertEqual(self.store.list_text_versions(self.document.id), [self.initial])
        self.assertEqual(self.store.get_document(self.document.id).current_version_id, self.initial.id)
        self.assertEqual([r.id for r in self.store.list_unfinished()], [record.id])

    def test_restart_reconstructs_all_feedback_in_chronological_order(self):
        first = self.new_candidate()
        self.workflow.decide(first.id, accept=False, rejection_reason="不要增强语气")
        request = self.workflow.get_retry_request(first.id)
        second = self.workflow.create_candidate(request, previous_record_id=first.id)
        self.workflow.decide(second.id, accept=False, rejection_reason="保留原来的词")
        workflow = RevisionWorkflow(generator=self.generator, store=JsonProjectStore(self.file), max_input_chars=1000)
        restored = workflow.get_retry_request(second.id)
        third = workflow.create_candidate(restored, previous_record_id=second.id)
        self.assertEqual([f.user_feedback for f in restored.feedback_history], ["不要增强语气", "保留原来的词"])
        self.assertEqual(restored.source_text, self.initial.content)
        self.assertEqual(self.generator.requests[-1], restored)
        self.assertEqual(third.document_id, self.document.id)
        self.assertEqual(third.base_version_id, self.initial.id)
        self.assertEqual([r.id for r in self.store.list_unfinished()], [third.id])

    def test_second_child_of_rejected_parent_is_rejected_before_generation(self):
        record = self.new_candidate()
        self.workflow.decide(record.id, accept=False, rejection_reason="保持语气")
        request = self.workflow.get_retry_request(record.id)
        self.workflow.create_candidate(request, previous_record_id=record.id)
        before = len(self.generator.requests)
        with self.assertRaises(InvalidDecisionError):
            self.workflow.create_candidate(request, previous_record_id=record.id)
        self.assertEqual(len(self.generator.requests), before)

    def test_pending_resume_accepts_without_model_or_key(self):
        record = self.new_candidate()
        workflow = RevisionWorkflow(generator=UnavailableCandidateGenerator(), store=JsonProjectStore(self.file), max_input_chars=1000)
        with redirect_stdout(StringIO()):
            decided = run_revision_session(workflow, lambda _: "y", record=self.store.get(record.id))
        self.assertEqual(decided.status, RevisionStatus.ACCEPTED)
        self.assertEqual(len(self.generator.requests), 1)

    def test_cancel_pending_keeps_formal_text_and_ends_task(self):
        record = self.new_candidate()
        self.workflow.close_task(record.id)
        self.assertEqual(self.store.get(record.id).status, RevisionStatus.CANCELLED)
        self.assertEqual(self.store.list_unfinished(), [])
        self.assertEqual(self.store.list_text_versions(self.document.id), [self.initial])
        with self.assertRaises(InvalidDecisionError):
            self.workflow.close_task(record.id)

    def test_abandon_rejected_task_preserves_reason_and_allows_new_task(self):
        record = self.new_candidate()
        self.workflow.decide(record.id, accept=False, rejection_reason="不要添加新词")
        self.workflow.close_task(record.id)
        saved = self.store.get(record.id)
        self.assertEqual(saved.status, RevisionStatus.REJECTED)
        self.assertEqual(saved.rejection_reason, "不要添加新词")
        self.assertIsNotNone(saved.abandoned_at)
        self.assertEqual(self.store.list_unfinished(), [])
        self.assertNotEqual(self.new_candidate().id, record.id)

    def test_unfinished_document_prevents_new_generation(self):
        self.new_candidate()
        with self.assertRaises(InvalidDecisionError):
            self.new_candidate()
        self.assertEqual(len(self.generator.requests), 1)

    def test_restore_copies_old_content_to_next_version(self):
        record = self.new_candidate()
        self.workflow.decide(record.id, accept=True)
        restored = self.documents.restore_version(self.document.id, self.initial.id)
        self.assertEqual(restored.version_number, 3)
        self.assertEqual(restored.content, self.initial.content)
        self.assertEqual(restored.restored_from_version_id, self.initial.id)
        self.assertIsNone(restored.source_revision_id)
        self.assertEqual([v.version_number for v in self.store.list_text_versions(self.document.id)], [1, 2, 3])
        self.assertEqual(self.store.get_document(self.document.id).current_version_id, restored.id)

    def test_restore_cannot_use_another_documents_version(self):
        other, version = self.documents.create_document(title="另一篇", initial_content="别的正文")
        before = self.file.read_bytes()
        with self.assertRaises(InvalidDecisionError):
            self.documents.restore_version(self.document.id, version.id)
        self.assertEqual(self.file.read_bytes(), before)

    def test_stale_candidate_cannot_overwrite_restored_current(self):
        record = self.new_candidate()
        restored = self.documents.restore_version(self.document.id, self.initial.id)
        before = self.file.read_bytes()
        with self.assertRaises(VersionConflictError):
            self.workflow.decide(record.id, accept=True)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.store.get(record.id).status, RevisionStatus.PENDING)
        self.assertEqual(self.store.get_document(self.document.id).current_version_id, restored.id)

    def test_stale_restore_confirmation_does_not_write(self):
        self.documents.restore_version(self.document.id, self.initial.id)
        before = self.file.read_bytes()
        with self.assertRaises(VersionConflictError):
            self.store.restore_version(self.document.id, self.initial.id, expected_current_version_id=self.initial.id)
        self.assertEqual(self.file.read_bytes(), before)

    def test_generation_detects_current_changed_while_model_was_working(self):
        def generate_and_restore(request):
            self.documents.restore_version(self.document.id, self.initial.id)
            return RevisionCandidate("候选", "说明")
        with patch.object(self.generator, "generate", side_effect=generate_and_restore):
            with self.assertRaises(VersionConflictError):
                self.new_candidate()
        self.assertEqual(self.store.list_all(), [])
        self.assertEqual(len(self.store.list_text_versions(self.document.id)), 2)

    def test_repeated_accept_does_not_append_duplicate_version(self):
        record = self.new_candidate()
        self.workflow.decide(record.id, accept=True)
        with self.assertRaises(InvalidDecisionError):
            self.workflow.decide(record.id, accept=True)
        self.assertEqual(len(self.store.list_text_versions(self.document.id)), 2)

    def test_failed_replace_keeps_record_pending_and_no_half_saved_version(self):
        record = self.new_candidate()
        before = self.file.read_bytes()
        with patch("writing_assistant.storage.json_store.os.replace", side_effect=OSError("模拟保存失败")):
            with self.assertRaises(StorageError):
                self.workflow.decide(record.id, accept=True)
        self.assertEqual(self.file.read_bytes(), before)
        self.assertEqual(self.store.get(record.id).status, RevisionStatus.PENDING)
        self.assertEqual(self.store.list_text_versions(self.document.id), [self.initial])
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_generation_failure_after_rejection_can_be_resumed(self):
        inputs = iter(["n", "不要改变原意"])
        record = self.new_candidate()
        with patch.object(self.generator, "generate", side_effect=GenerationError("模拟网络失败")):
            with redirect_stdout(StringIO()), self.assertRaises(GenerationError):
                run_revision_session(self.workflow, lambda _: next(inputs), record=record)
        saved = self.store.get(record.id)
        self.assertEqual(saved.status, RevisionStatus.REJECTED)
        self.assertEqual([r.id for r in self.store.list_unfinished()], [record.id])
        request = self.workflow.get_retry_request(record.id)
        self.assertEqual(request.feedback_history[0].user_feedback, "不要改变原意")

    def test_corrupt_relationship_stops_write_and_keeps_file_unchanged(self):
        raw = json.loads(self.file.read_text(encoding="utf-8"))
        raw["documents"][0]["current_version_id"] = "不存在的版本"
        self.file.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        before = self.file.read_bytes()
        with self.assertRaises(StorageError):
            self.documents.create_document(title="新文档", initial_content="新正文")
        self.assertEqual(self.file.read_bytes(), before)

    def test_legacy_read_is_readonly_and_first_write_creates_exact_backup(self):
        legacy = self.root / "legacy.json"
        legacy_bytes = b'{"schema_version":1,"revisions":[]}\n'
        legacy.write_bytes(legacy_bytes)
        store = JsonProjectStore(legacy)
        self.assertEqual(store.list_all(), [])
        self.assertEqual(legacy.read_bytes(), legacy_bytes)
        self.assertFalse(store.legacy_backup_path.exists())
        DocumentWorkflow(store=store, max_content_chars=100).create_document(title="新文档", initial_content="正文")
        self.assertEqual(store.legacy_backup_path.read_bytes(), legacy_bytes)
        self.assertEqual(json.loads(legacy.read_text(encoding="utf-8"))["schema_version"], 2)

    def test_main_menu_continues_after_create_list_and_invalid_document(self):
        inputs = iter(["3", "新文章", "初稿", "4", "5", "不存在", "0"])
        output = StringIO()
        with patch("writing_assistant.cli.load_config", return_value=self.config), redirect_stdout(output):
            result = main([], input_function=lambda _: next(inputs))
        self.assertEqual(result, 0)
        self.assertIn("共有 2 篇文档", output.getvalue())
        self.assertIn("已返回主菜单", output.getvalue())

    def test_demo_does_not_read_env_or_build_real_generator(self):
        inputs = iter(["1", "原文", "精简", "y", "0"])
        with patch("writing_assistant.cli.get_project_root", return_value=self.root):
            with patch("writing_assistant.cli.load_config") as load, patch("writing_assistant.cli.DeepSeekCandidateGenerator") as real:
                with redirect_stdout(StringIO()):
                    self.assertEqual(main(["--demo"], input_function=lambda _: next(inputs)), 0)
        load.assert_not_called()
        real.assert_not_called()
        self.assertTrue((self.root / "data/demo-project.json").exists())
        self.assertEqual(self.store.list_all(), [])

    def test_resume_pending_through_menu_without_key(self):
        record = self.new_candidate()
        inputs = iter([record.id, "y"])
        with redirect_stdout(StringIO()):
            run_action("10", self.config, JsonProjectStore(self.file), lambda _: next(inputs), demo=False)
        self.assertEqual(self.store.get(record.id).status, RevisionStatus.ACCEPTED)


if __name__ == "__main__":
    unittest.main()
