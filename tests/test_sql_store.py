"""数据库约束、迁移幂等与创建事务回滚测试。"""

from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant.document_workflow import DocumentCatalogWorkflow
from writing_assistant.errors import DocumentNotFoundError, StorageError
from writing_assistant.models import Document, TextVersion
from writing_assistant.storage.database import Database
from writing_assistant.storage.sql_store import SqlDocumentStore
from writing_assistant.storage.tables import Base, DocumentRow, TextVersionRow


class SqlDocumentStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Database(Path(temporary.name) / "writing.db")
        self.addCleanup(self.database.dispose)
        self.database.initialize()

    def create_document(self, title="标题"):
        with self.database.sessions() as session:
            workflow = DocumentCatalogWorkflow(store=SqlDocumentStore(session), max_content_chars=10_000)
            return workflow.create_document(title=title, initial_content="正文")

    def counts(self):
        with self.database.sessions() as session:
            return (session.scalar(select(func.count()).select_from(DocumentRow)),
                    session.scalar(select(func.count()).select_from(TextVersionRow)))

    def test_initial_migration_is_versioned_and_repeated_startup_preserves_data(self):
        document, version = self.create_document()
        self.database.initialize()
        with self.database.sessions() as session:
            self.assertEqual(session.scalar(text("SELECT version_num FROM alembic_version")), "0001_document_catalog")
            store = SqlDocumentStore(session)
            self.assertEqual(store.get_document(document.id), document)
            self.assertEqual(store.get_text_version(version.id), version)
        self.assertEqual(self.counts(), (1, 1))

    def test_migration_schema_matches_orm_metadata(self):
        with self.database.engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            self.assertEqual(compare_metadata(context, Base.metadata), [])

    def test_second_insert_failure_rolls_back_document_and_allows_retry(self):
        document, version = Document.create_with_initial_version(title="标题", initial_content="正文")
        with self.database.sessions() as session:
            original_execute = session.execute
            call_count = 0

            def fail_second(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise OperationalError("INSERT", {}, Exception("模拟正文保存失败"))
                return original_execute(*args, **kwargs)

            with patch.object(session, "execute", side_effect=fail_second):
                with self.assertRaises(StorageError):
                    SqlDocumentStore(session).add_document(document, version)
            self.assertFalse(session.in_transaction())
            SqlDocumentStore(session).add_document(document, version)
        self.assertEqual(self.counts(), (1, 1))

    def test_failure_before_commit_leaves_neither_half_of_new_document(self):
        document, version = Document.create_with_initial_version(title="标题", initial_content="正文")
        with self.database.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TRIGGER reject_new_version BEFORE INSERT ON text_versions "
                "BEGIN SELECT RAISE(ABORT, '模拟写入失败'); END"
            )
        with self.database.sessions() as session:
            with self.assertRaises(StorageError):
                SqlDocumentStore(session).add_document(document, version)
        self.assertEqual(self.counts(), (0, 0))

    def test_deferred_foreign_key_rejects_document_without_its_version_at_commit(self):
        document, _version = Document.create_with_initial_version(title="标题", initial_content="正文")
        with self.assertRaises(IntegrityError):
            with self.database.engine.begin() as connection:
                connection.execute(insert(DocumentRow).values(**document.to_dict()))
        self.assertEqual(self.counts(), (0, 0))

    def test_current_version_cannot_belong_to_another_document(self):
        first, _first_version = self.create_document("第一篇")
        _second, second_version = self.create_document("第二篇")
        with self.assertRaises(IntegrityError):
            with self.database.engine.begin() as connection:
                connection.execute(update(DocumentRow).where(DocumentRow.id == first.id).values(current_version_id=second_version.id))
        with self.database.sessions() as session:
            self.assertEqual(SqlDocumentStore(session).get_document(first.id), first)

    def test_duplicate_version_number_is_rejected(self):
        document, _initial = self.create_document()
        duplicate = TextVersion.create_initial(document_id=document.id, content="另一份正文")
        with self.assertRaises(IntegrityError):
            with self.database.engine.begin() as connection:
                connection.execute(insert(TextVersionRow).values(**duplicate.to_dict()))
        self.assertEqual(self.counts(), (1, 1))

    def test_rename_missing_document_rolls_back_and_existing_document_survives(self):
        document, _version = self.create_document()
        with self.database.sessions() as session:
            store = SqlDocumentStore(session)
            with self.assertRaises(DocumentNotFoundError):
                store.rename_document(str(uuid4()), "新标题")
            self.assertFalse(session.in_transaction())
            renamed = store.rename_document(document.id, "  新标题  ")
            self.assertEqual(renamed.title, "新标题")
        self.assertEqual(self.counts(), (1, 1))

    def test_inconsistent_initial_objects_do_not_write_anything(self):
        document, initial = Document.create_with_initial_version(title="标题", initial_content="正文")
        with self.database.sessions() as session:
            with self.assertRaises(StorageError):
                SqlDocumentStore(session).add_document(document, replace(initial, document_id=str(uuid4())))
        self.assertEqual(self.counts(), (0, 0))

    def test_foreign_keys_are_enabled_for_every_new_connection(self):
        with self.database.engine.connect() as first:
            with self.database.engine.connect() as second:
                self.assertEqual(first.scalar(text("PRAGMA foreign_keys")), 1)
                self.assertEqual(second.scalar(text("PRAGMA foreign_keys")), 1)
