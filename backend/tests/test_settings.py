"""配置文件与进程环境的优先级，不读取真实凭证。"""

import os

from writing_server import settings as configuration


def test_database_file_precedes_empty_example_and_environment_wins(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "environ", {})
    monkeypatch.setattr(configuration, "project_root", lambda: tmp_path)
    (tmp_path / ".env").write_text("WRITING_DB_PASSWORD=\nWRITING_PROVIDER=demo\n", encoding="utf-8")
    (tmp_path / ".env.postgres.local").write_text(
        "WRITING_DB_PASSWORD=local-test-value\nWRITING_DB_MIGRATION_PASSWORD=migration-test-value\n", encoding="utf-8"
    )
    first = configuration.Settings.load()
    assert first.database_url.password == "local-test-value"
    assert first.migration_url.password == "migration-test-value"
    assert "local-test-value" not in repr(first)
    monkeypatch.setenv("WRITING_DB_PASSWORD", "process-test-value")
    second = configuration.Settings.load()
    assert second.database_url.password == "process-test-value"
