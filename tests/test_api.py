"""HTTP 文档接口的输入、分页、持久化与失败隔离测试。"""

from contextlib import contextmanager
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from writing_assistant import __version__
from writing_assistant.api.app import create_app
from writing_assistant.errors import StorageError
from writing_assistant.storage.sql_store import SqlDocumentStore
from writing_assistant.storage.tables import DocumentRow, TextVersionRow


class DocumentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "nested" / "writing.db"
        self.app = create_app(self.path)
        self.client = self.enterContext(TestClient(self.app))

    def create_document(self, title="项目介绍", content="初始正文。"):
        response = self.client.post("/api/documents", json={"title": title, "initial_content": content})
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def test_health_and_interactive_schema_are_available_without_model_key(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok", "database": "ok"})
        self.assertEqual(self.client.get("/docs").status_code, 200)
        schema = self.client.get("/openapi.json").json()
        self.assertEqual(schema["info"]["version"], __version__)
        self.assertIn("post", schema["paths"]["/api/documents"])
        self.assertIn("patch", schema["paths"]["/api/documents/{document_id}"])

    def test_create_normalizes_and_open_returns_same_initial_version(self):
        response = self.create_document("  项目介绍  ", "  初始正文。\n")
        body = response.json()
        self.assertEqual(body["title"], "项目介绍")
        self.assertEqual(body["current_version"]["content"], "初始正文。")
        self.assertEqual(body["current_version"]["version_number"], 1)
        self.assertEqual(body["current_version"]["document_id"], body["id"])
        self.assertEqual(body["current_version"]["id"], body["current_version_id"])
        opened = self.client.get(response.headers["location"])
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.json(), body)

    def test_missing_and_malformed_ids_have_distinct_client_errors(self):
        for method in ("get", "patch"):
            kwargs = {"json": {"title": "新标题"}} if method == "patch" else {}
            with self.subTest(method=method):
                missing = getattr(self.client, method)(f"/api/documents/{uuid4()}", **kwargs)
                self.assertEqual(missing.status_code, 404)
                self.assertEqual(missing.json(), {"detail": "找不到指定文档"})
                self.assertEqual(getattr(self.client, method)("/api/documents/not-a-uuid", **kwargs).status_code, 422)

    def test_invalid_create_never_writes_a_document_or_version(self):
        invalid = [
            {}, {"title": "标题"},
            {"title": " \n ", "initial_content": "正文"},
            {"title": "标题", "initial_content": "\t "},
            {"title": 123, "initial_content": "正文"},
            {"title": "标题", "initial_content": None},
            {"title": "长" * 201, "initial_content": "正文"},
            {"title": "标题", "initial_content": "长" * 10_001},
            {"title": "标题", "initial_content": "正文", "id": str(uuid4())},
        ]
        for body in invalid:
            with self.subTest(body=str(body)[:70]):
                self.assertEqual(self.client.post("/api/documents", json=body).status_code, 422)
        with self.app.state.database.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(DocumentRow)), 0)
            self.assertEqual(session.scalar(select(func.count()).select_from(TextVersionRow)), 0)

    def test_document_limits_accept_the_exact_boundary(self):
        body = self.create_document("题" * 200, "文" * 10_000).json()
        self.assertEqual(len(body["title"]), 200)
        self.assertEqual(len(body["current_version"]["content"]), 10_000)

    def test_pagination_is_deterministic_complete_and_does_not_return_bodies(self):
        created = [self.create_document(f"文档{number}").json() for number in range(5)]
        expected = sorted(created, key=lambda doc: (doc["created_at"], doc["id"]), reverse=True)
        ids = []
        for page in (1, 2, 3):
            response = self.client.get("/api/documents", params={"page": page, "page_size": 2})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual((data["total"], data["page"], data["page_size"]), (5, page, 2))
            self.assertTrue(all("current_version" not in item for item in data["items"]))
            ids.extend(item["id"] for item in data["items"])
        self.assertEqual(ids, [doc["id"] for doc in expected])
        self.assertEqual(self.client.get("/api/documents?page=4&page_size=2").json()["items"], [])

    def test_invalid_pagination_is_rejected(self):
        for query in ("page=0", "page=-1", "page=1000001", "page=no", "page_size=0", "page_size=101"):
            with self.subTest(query=query):
                self.assertEqual(self.client.get(f"/api/documents?{query}").status_code, 422)

    def test_rename_changes_only_metadata_and_keeps_initial_content(self):
        created = self.create_document().json()
        response = self.client.patch(f"/api/documents/{created['id']}", json={"title": " 新标题 "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["title"], "新标题")
        self.assertEqual(response.json()["current_version_id"], created["current_version_id"])
        opened = self.client.get(f"/api/documents/{created['id']}").json()
        self.assertEqual(opened["current_version"], created["current_version"])
        with self.app.state.database.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(TextVersionRow)), 1)

    def test_invalid_or_extra_patch_fields_cannot_change_saved_data(self):
        original = self.create_document().json()
        url = f"/api/documents/{original['id']}"
        for body in ({}, {"title": " "}, {"title": 1}, {"title": "新标题", "current_version_id": str(uuid4())}):
            with self.subTest(body=body):
                self.assertEqual(self.client.patch(url, json=body).status_code, 422)
                self.assertEqual(self.client.get(url).json(), original)

    def test_restarting_application_preserves_documents_and_schema(self):
        created = self.create_document().json()
        second_app = create_app(self.path)
        with TestClient(second_app) as client:
            self.assertEqual(client.get(f"/api/documents/{created['id']}").json(), created)
            self.assertEqual(client.get("/api/documents").json()["total"], 1)

    def test_storage_failure_is_sanitized_and_next_request_still_works(self):
        with patch.object(SqlDocumentStore, "add_document", side_effect=StorageError("内部路径与敏感细节")):
            with self.assertLogs("writing_assistant.api.app", level="ERROR"):
                response = self.client.post("/api/documents", json={"title": "标题", "initial_content": "正文"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"detail": "数据库操作失败，请稍后重试"})
        self.assertNotIn("敏感", response.text)
        self.assertEqual(self.client.get("/api/documents").json()["total"], 0)
        self.create_document()

    def test_sessions_are_per_request_and_always_closed(self):
        sessions = []
        closed = []
        factory = self.app.state.database.sessions

        @contextmanager
        def tracking_session():
            with factory() as session:
                sessions.append(session)
                yield session
            closed.append(session)

        with patch.object(self.app.state.database, "sessions", side_effect=tracking_session):
            self.create_document()
            self.client.get("/api/documents")
        self.assertEqual(len(sessions), 2)
        self.assertIsNot(sessions[0], sessions[1])
        self.assertEqual(closed, sessions)
        self.assertTrue(all(not session.in_transaction() for session in sessions))

    def test_app_construction_does_not_create_database_or_read_model_config(self):
        other = self.path.parent / "unused.db"
        with patch("writing_assistant.config.load_config", side_effect=AssertionError("不应读取模型配置")):
            app = create_app(other)
            self.assertFalse(other.exists())
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").status_code, 200)

    def test_titles_are_parameter_bound_instead_of_executed_as_sql(self):
        suspicious = "标题'); DROP TABLE documents; --"
        body = self.create_document(suspicious).json()
        self.assertEqual(body["title"], suspicious)
        self.assertEqual(self.client.get("/api/documents").json()["total"], 1)
