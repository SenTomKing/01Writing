from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, text

from writing_server.database import Database
from writing_server.generation import Generation, ModelFailure
from writing_server.jobs import dispatch, process_task, recover
from writing_server.tables import Attempt, Document, Task, User, now
from writing_assistant.models import RevisionCandidate


def enqueue(client, doc, key="request-one", instruction="保持数字，让表达简洁"):
    response = client.post(
        f"/api/v1/documents/{doc['id']}/tasks",
        headers={"Idempotency-Key": key},
        json={"instruction": instruction, "base_version_id": doc["current_version_id"]},
    )
    assert response.status_code == 202, response.text
    return response.json()


def complete(app, settings, task, generator=None):
    return process_task(app.state.database, settings, task["id"], **({"generator": generator} if generator else {}))


def test_health_and_password_session_security(client, app):
    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/auth/me").json()["username"] == "tester"
    with app.state.database.sessions() as session:
        row = session.scalar(select(User))
        assert row.password_hash.startswith("$argon2id$")
        assert row.password_hash != "test-password-12345"
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 401


def test_csrf_and_origin_checks(client, doc):
    path = f"/api/v1/documents/{doc['id']}"
    assert client.patch(path, json={"title": "被拦截"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert (
        client.patch(path, json={"title": "被拦截"}, headers={"Origin": "https://attacker.example"}).status_code == 403
    )
    assert client.get(path).json()["title"] == "实验记录"


def test_validation_never_saves_empty_or_oversized_content(client):
    for content in ("   ", "a" * 20001):
        assert client.post("/api/v1/documents", json={"title": "标题", "content": content}).status_code == 422
    assert client.get("/api/v1/documents").json()["total"] == 0
    assert client.post("/api/v1/documents", content=b"x" * 128001).status_code == 413


def test_document_versions_edit_restore_export_and_conflict(client, doc):
    path = f"/api/v1/documents/{doc['id']}"
    saved = client.post(
        path + "/versions", json={"content": "第二版正文。", "base_version_id": doc["current_version_id"]}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["current_version"]["number"] == 2
    assert (
        client.post(
            path + "/versions", json={"content": "过期改稿。", "base_version_id": doc["current_version_id"]}
        ).status_code
        == 409
    )
    restored = client.post(
        path + f"/versions/{doc['current_version_id']}/restore",
        json={"base_version_id": saved.json()["current_version_id"]},
    )
    assert restored.json()["current_version"]["number"] == 3
    assert restored.json()["current_version"]["content"] == doc["current_version"]["content"]
    assert client.get(path + "/versions").json()["total"] == 3
    assert client.get(path + "/export?format=md").text.startswith("# 实验记录")
    assert client.get(path + "/export?format=txt").text == doc["current_version"]["content"]


def test_idempotent_generation_and_accept_only_adds_one_version(client, app, settings, doc):
    task = enqueue(client, doc)
    assert enqueue(client, doc)["id"] == task["id"]
    assert complete(app, settings, task)
    assert not complete(app, settings, task)
    for _ in range(2):
        response = client.post(f"/api/v1/tasks/{task['id']}/accept")
        assert response.status_code == 200, response.text
    assert client.get(f"/api/v1/documents/{doc['id']}/versions").json()["total"] == 2
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["runs"][0]["outcome"] == "succeeded"


def test_idempotency_key_cannot_change_request(client, doc):
    enqueue(client, doc)
    response = client.post(
        f"/api/v1/documents/{doc['id']}/tasks",
        headers={"Idempotency-Key": "request-one"},
        json={"instruction": "不同要求", "base_version_id": doc["current_version_id"]},
    )
    assert response.status_code == 409


def test_rejection_persists_and_retry_passes_feedback(client, app, settings, doc):
    task = enqueue(client, doc)
    complete(app, settings, task)
    assert (
        client.post(f"/api/v1/tasks/{task['id']}/reject", json={"reason": "仅去掉啊，保留数值与语气"}).status_code
        == 200
    )
    retried = client.post(f"/api/v1/tasks/{task['id']}/retry", headers={"Idempotency-Key": "request-two"}).json()
    captured = []

    def generator(settings, **kwargs):
        captured.append(kwargs)
        return Generation(RevisionCandidate("实验组 12.4%，对照组 8.1%。这个结果很好。", "删除口语"), None)

    complete(app, settings, retried, generator)
    assert captured[0]["feedback"][-1]["feedback"] == "仅去掉啊，保留数值与语气"
    assert captured[0]["source"] == doc["current_version"]["content"]
    assert client.get(f"/api/v1/documents/{doc['id']}").json()["current_version_id"] == doc["current_version_id"]


def test_old_candidate_cannot_overwrite_saved_text(client, app, settings, doc):
    task = enqueue(client, doc)
    complete(app, settings, task)
    path = f"/api/v1/documents/{doc['id']}"
    client.post(path + "/versions", json={"content": "人工更新", "base_version_id": doc["current_version_id"]})
    assert client.post(f"/api/v1/tasks/{task['id']}/accept").status_code == 409
    assert client.get(path).json()["current_version"]["content"] == "人工更新"


def test_account_isolation_covers_export_tasks_events_history(client, app, doc):
    task = enqueue(client, doc)
    with TestClient(app) as other:
        response = other.post("/api/v1/auth/register", json={"username": "other", "password": "other-password-123"})
        other.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        assert other.get("/api/v1/documents").json()["total"] == 0
        for path in (
            f"/documents/{doc['id']}",
            f"/documents/{doc['id']}/export",
            f"/documents/{doc['id']}/versions",
            f"/documents/{doc['id']}/tasks",
            f"/tasks/{task['id']}",
            f"/tasks/{task['id']}/events",
        ):
            assert other.get("/api/v1" + path).status_code == 404, path
        assert other.post(f"/api/v1/tasks/{task['id']}/cancel").status_code == 404


def test_cancel_during_model_discards_late_result(client, app, settings, doc):
    task = enqueue(client, doc)

    def generator(settings, **kwargs):
        assert client.post(f"/api/v1/tasks/{task['id']}/cancel").status_code == 200
        return Generation(RevisionCandidate("迟到结果", "不能写入"), None)

    assert not complete(app, settings, task, generator)
    response = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert response["status"] == "cancelled" and response["candidate"] is None
    assert response["runs"][0]["outcome"] == "discarded"


def test_invalid_model_output_preserves_original(client, app, settings, doc):
    task = enqueue(client, doc)

    def generator(settings, **kwargs):
        raise ModelFailure("invalid_model_output", "输出不是合法 JSON")

    complete(app, settings, task, generator)
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "failed"
    assert client.get(f"/api/v1/documents/{doc['id']}/versions").json()["total"] == 1


def test_temporary_failure_has_bounded_retry(client, app, settings, doc):
    task = enqueue(client, doc)

    def fail(settings, **kwargs):
        raise ModelFailure("model_timeout", "超时", retryable=True)

    for _ in range(settings.max_attempts):
        complete(app, settings, task, fail)
    response = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert response["status"] == "failed" and len(response["runs"]) == 3


def test_expired_worker_claim_is_recovered(client, app, settings, doc):
    task = enqueue(client, doc)
    with app.state.database.sessions.begin() as session:
        row = session.get(Task, task["id"])
        row.status = "running"
        row.claim_token = "crashed"
        row.attempts = 1
        row.lease_until = now() - timedelta(seconds=1)
        session.add(
            Attempt(task_id=row.id, number=1, claim_token="crashed", provider="demo", model="demo", prompt_version="v1")
        )
    assert recover(app.state.database, settings) == 1
    assert complete(app, settings, task)
    result = client.get(f"/api/v1/tasks/{task['id']}").json()
    assert result["status"] == "review" and result["runs"][0]["outcome"] == "interrupted"


def test_broker_failure_leaves_durable_dispatch_intent(client, app, doc):
    task = enqueue(client, doc)

    def unavailable(task_id):
        raise OSError("broker unavailable")

    assert dispatch(app.state.database, unavailable) == 0
    with app.state.database.sessions() as session:
        row = session.get(Task, task["id"])
        assert row.status == "queued" and row.dispatch_after > row.created_at


def test_postgres_deferred_constraint_protects_cross_document_pointer(client, settings, doc):
    other = client.post("/api/v1/documents", json={"title": "另一篇", "content": "不能串稿"}).json()
    owner = Database(settings, migration=True)
    import pytest
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with owner.sessions.begin() as session:
            row = session.get(Document, doc["id"])
            row.current_version_id = other["current_version_id"]
    owner.close()


def test_app_credentials_cannot_create_tables(settings):
    import pytest
    from sqlalchemy.exc import ProgrammingError

    db = Database(settings)
    with pytest.raises(ProgrammingError):
        with db.engine.begin() as connection:
            connection.execute(text("CREATE TABLE should_not_exist (id int)"))
    db.close()


def test_stream_contains_saved_events_after_restart(client, app, settings, doc):
    task = enqueue(client, doc)
    complete(app, settings, task)
    response = client.get(f"/api/v1/tasks/{task['id']}/events")
    assert response.status_code == 200 and "event: progress" in response.text and '"kind": "review"' in response.text
    assert "text/event-stream" in response.headers["content-type"]


def test_model_is_not_called_with_open_database_transaction(client, app, settings, doc):
    task = enqueue(client, doc)

    def generator(settings, **kwargs):
        assert app.state.database.engine.pool.checkedout() == 0
        return Generation(RevisionCandidate("可用正文", "说明"), None)

    complete(app, settings, task, generator)
    assert client.get(f"/api/v1/tasks/{task['id']}").json()["status"] == "review"
