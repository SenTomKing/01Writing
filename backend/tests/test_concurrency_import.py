from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from sqlalchemy import select, func
from writing_server import services
from writing_server.importer import import_json
from writing_server.jobs import process_task
from writing_server.tables import User, Task
from writing_assistant.models import Document, RevisionCandidate
from writing_assistant.storage.json_store import JsonProjectStore


def test_concurrent_generation_same_key_creates_one_task(client, app, settings, doc):
    with app.state.database.sessions() as session:
        user = session.scalar(select(User))
        user_id = user.id
    gate = Barrier(2)

    def submit():
        gate.wait()
        with app.state.database.sessions.begin() as session:
            task = services.create_task(
                session, user_id, doc["id"], "简洁", doc["current_version_id"], "concurrent-key", settings
            )
            return task.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert results[0] == results[1]
    with app.state.database.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Task)) == 1


def test_concurrent_accept_appends_only_once(client, app, settings, doc):
    response = client.post(
        f"/api/v1/documents/{doc['id']}/tasks",
        headers={"Idempotency-Key": "parallel-accept"},
        json={"instruction": "简洁", "base_version_id": doc["current_version_id"]},
    )
    task_id = response.json()["id"]
    process_task(app.state.database, settings, task_id)
    with app.state.database.sessions() as session:
        user_id = session.scalar(select(User.id))
    gate = Barrier(2)

    def accept():
        gate.wait()
        with app.state.database.sessions.begin() as session:
            return services.decide(session, user_id, task_id, "accept").status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: accept(), range(2))) == ["accepted", "accepted"]
    assert client.get(f"/api/v1/documents/{doc['id']}/versions").json()["total"] == 2


def test_import_preserves_source_and_deduplicates(client, app, tmp_path):
    path = tmp_path / "legacy.json"
    store = JsonProjectStore(path)
    document, version = Document.create_with_initial_version(title="旧稿", initial_content="旧版正文啊")
    store.add_document(document, version)
    before = path.read_bytes()
    first = import_json(app.state.database, path, "tester")
    second = import_json(app.state.database, path, "tester")
    assert first["mapping"] == second["mapping"]
    assert path.read_bytes() == before
    assert client.get("/api/v1/documents").json()["total"] == 1


def test_invalid_candidate_object_fails_instead_of_sticking_running(client, app, settings, doc):
    from writing_server.generation import Generation

    response = client.post(
        f"/api/v1/documents/{doc['id']}/tasks",
        headers={"Idempotency-Key": "broken-provider"},
        json={"instruction": "简洁", "base_version_id": doc["current_version_id"]},
    )
    task_id = response.json()["id"]
    process_task(
        app.state.database,
        settings,
        task_id,
        lambda settings, **kwargs: Generation(RevisionCandidate("", "空输出"), None),
    )
    assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "failed"
