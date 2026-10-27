"""在独立 PostgreSQL 模式中测试；从不清空 public 或个人文档。"""

from dataclasses import replace
import re
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from writing_server.app import create_app
from writing_server.database import Database
from writing_server.settings import Settings
from writing_server.tables import Base


@pytest.fixture(scope="session")
def database_settings():
    base = Settings.load()
    schema = "writing_test_" + uuid4().hex
    assert re.fullmatch(r"writing_test_[0-9a-f]{32}", schema)
    admin = create_engine(base.migration_url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO writing_app'))

    def url(original):
        value = make_url(original) if isinstance(original, str) else original
        return value.update_query_dict({"options": f"-csearch_path={schema}"})

    settings = replace(
        base,
        database_url=url(base.database_url),
        migration_url=url(base.migration_url),
        provider="demo",
        search_key="", embedding_key="", embedding_url="", embedding_model="",
        origins=("http://testserver",),
        static_dir=None,
    )
    migration = Database(settings, migration=True)
    migration.migrate()
    with migration.engine.begin() as connection:
        connection.execute(
            text(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "{schema}" TO writing_app')
        )
        connection.execute(text(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "{schema}" TO writing_app'))
    yield settings
    migration.close()
    with admin.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    admin.dispose()


@pytest.fixture
def settings(database_settings):
    owner = Database(database_settings, migration=True)
    names = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    with owner.engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    owner.close()
    return database_settings


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as browser:
        response = browser.post("/api/v1/auth/register", json={"username": "tester", "password": "test-password-12345"})
        assert response.status_code == 201, response.text
        browser.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        yield browser


@pytest.fixture
def doc(client):
    response = client.post(
        "/api/v1/documents", json={"title": "实验记录", "content": "实验组 12.4%，对照组 8.1%。这个结果很好啊。"}
    )
    assert response.status_code == 201, response.text
    return response.json()
