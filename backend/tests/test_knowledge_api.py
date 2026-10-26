from fastapi.testclient import TestClient
from sqlalchemy import select

from writing_server.jobs import process_task
from writing_server.tables import Task


def add(client, kind="source", **kwargs):
    response = client.post(
        "/api/v1/knowledge",
        json={
            "kind": kind,
            "title": "实验资料" if kind == "source" else "写作偏好",
            "content": "实验组 12.4%，对照组 8.1%。",
            **kwargs,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def enqueue(client, doc, sources, memories=()):
    return client.post(
        f"/api/v1/documents/{doc['id']}/tasks",
        headers={"Idempotency-Key": "agent-test-key"},
        json={
            "instruction": "保持数字，删除口语",
            "base_version_id": doc["current_version_id"],
            "strategy": "agent",
            "source_ids": sources,
            "memory_ids": list(memories),
        },
    )


def test_agent_end_to_end_and_immutable_snapshot(client, app, settings, doc):
    source, memory = add(client), add(client, "memory", content="不要夸大结论")
    response = enqueue(client, doc, [source["id"]], [memory["id"]])
    assert response.status_code == 202, response.text
    task = response.json()
    assert (
        client.patch(
            "/api/v1/knowledge/" + source["id"], json={"kind": "source", "title": "新资料", "content": "后来更新的内容"}
        ).status_code
        == 200
    )
    assert process_task(app.state.database, settings, task["id"])
    result = client.get("/api/v1/tasks/" + task["id"]).json()
    assert result["status"] == "review" and len(result["trace"]) == 3
    assert result["citations"][0]["quote"] == source["content"]
    assert client.get(f"/api/v1/documents/{doc['id']}").json()["current_version"]["number"] == 1
    assert client.post(f"/api/v1/tasks/{task['id']}/accept").status_code == 200
    assert client.get(f"/api/v1/documents/{doc['id']}").json()["current_version"]["number"] == 2


def test_knowledge_scope_expiry_delete_and_csrf(client, app, doc):
    source = add(client)
    with TestClient(app) as other:
        auth = other.post("/api/v1/auth/register", json={"username": "other", "password": "test-password-12345"}).json()
        other.headers["X-CSRF-Token"] = auth["csrf_token"]
        assert other.get("/api/v1/knowledge").json() == []
        assert other.delete("/api/v1/knowledge/" + source["id"]).status_code == 404
        otherdoc = other.post("/api/v1/documents", json={"title": "别的文档", "content": "无权使用他人资料"}).json()
        assert enqueue(other, otherdoc, [source["id"]]).status_code == 404
    assert client.delete("/api/v1/knowledge/" + source["id"], headers={"X-CSRF-Token": "bad"}).status_code == 403
    expired = add(client, "memory", expires_at="2020-01-01T00:00:00Z")
    assert enqueue(client, doc, [], [expired["id"]]).status_code == 409
    assert client.delete("/api/v1/knowledge/" + source["id"]).status_code == 200
    assert enqueue(client, doc, [source["id"]]).status_code == 404


def test_agent_cancel_blocks_checkpoint_and_candidate(client, app, settings, doc, monkeypatch):
    source = add(client)
    task = enqueue(client, doc, [source["id"]]).json()
    from writing_server import agent

    original = agent.demo_response

    def cancel(*args):
        client.post("/api/v1/tasks/" + task["id"] + "/cancel")
        return original(*args)

    monkeypatch.setattr(agent, "demo_response", cancel)
    assert not process_task(app.state.database, settings, task["id"])
    result = client.get("/api/v1/tasks/" + task["id"]).json()
    assert result["status"] == "cancelled" and result["candidate"] is None
    with app.state.database.sessions() as session:
        row = session.scalar(select(Task).where(Task.id == task["id"]))
        assert row.checkpoint["reserved_calls"] == 1


def test_rejected_retry_retains_deleted_source_snapshot(client, app, settings, doc):
    source = add(client)
    task = enqueue(client, doc, [source["id"]]).json()
    assert process_task(app.state.database, settings, task["id"])
    assert client.post(f"/api/v1/tasks/{task['id']}/reject", json={"reason": "只删除口语"}).status_code == 200
    assert client.delete("/api/v1/knowledge/" + source["id"]).status_code == 200
    response = client.post(f"/api/v1/tasks/{task['id']}/retry", headers={"Idempotency-Key": "retry-deleted-source"})
    assert response.status_code == 202, response.text
    retried = response.json()
    assert retried["strategy"] == "agent"
    assert process_task(app.state.database, settings, retried["id"])
    result = client.get("/api/v1/tasks/" + retried["id"]).json()
    assert result["status"] == "review"
    assert result["citations"][0]["quote"] == source["content"]
