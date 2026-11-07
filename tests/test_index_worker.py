from fastapi.testclient import TestClient

from web.api import create_app
from storage.database import Database
from storage.migration import upgrade_database
from knowledge.document_service import DocumentService
from knowledge.document_lifecycle import DocumentLifecycle
from knowledge.index_worker import IndexWorker, ingest_document_from_disk


class FakeModel:
    model_name = "test-model"

    def __init__(self):
        self.fail = False
        self.encoded = []

    def encode(self, text):
        if self.fail:
            raise RuntimeError("模型爆炸")
        self.encoded.append(text)
        return [0.1, 0.2]


class FakeStore:
    def __init__(self):
        self.inserted = []
        self.deleted = []

    def insert_chunk(self, session, *, owner_id, chunk, vector, document_version):
        self.inserted.append((owner_id, chunk.source, chunk.text, document_version))

    def delete_document_version(self, session, *, owner_id, source, document_version):
        self.deleted.append((owner_id, source, document_version))
        return 1


def make_client(tmp_path, *, model=None):
    model = model or FakeModel()
    url = f"sqlite+pysqlite:///{(tmp_path / 'index-worker.db').as_posix()}"
    upgrade_database(url)
    database = Database(url)
    store = FakeStore()
    service = DocumentService(database.sessions, store, model)
    upload_dir = tmp_path / "uploads"
    lifecycle = DocumentLifecycle(database.sessions)
    worker = IndexWorker(
        upload_dir,
        lambda document_id: ingest_document_from_disk(
            upload_dir, document_id, database.sessions, store, model, lifecycle
        ),
        start_in_thread=False,
    )
    app = create_app(
        url,
        token_secret="test-secret-key-at-least-32-bytes",
        settings_factory=lambda: object(),
        document_service_factory=lambda: service,
        index_worker_factory=lambda: worker,
        upload_dir=upload_dir,
    )
    return TestClient(app), database, store, worker


def auth_headers(client, username):
    password = "password-123"
    client.post("/auth/register", json={"username": username, "password": password})
    token = client.post(
        "/auth/login",
        data={"username": username, "password": password},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def upload_file(client, headers, *, filename, content, version="v1"):
    return client.post(
        "/documents/file",
        files={"file": (filename, content, "text/markdown")},
        data={"document_version": version, "chunk_size": "100", "overlap": "10"},
        headers=headers,
    )


def test_file_upload_returns_pending_and_persists_raw_file(tmp_path):
    client, database, store, _ = make_client(tmp_path)
    alice = auth_headers(client, "alice")

    response = upload_file(client, alice, filename="notes.md", content="文件正文：幂等操作会避免重复写入。")

    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "notes.md"
    assert body["state"] == "pending"
    assert body["chunks_ingested"] == 0
    assert store.inserted == []

    upload_dir = tmp_path / "uploads"
    assert (upload_dir / f"{body['id']}.upload").read_bytes().decode("utf-8").startswith("文件正文")
    assert (upload_dir / f"{body['id']}.json").exists()

    listed = client.get("/documents", headers=alice).json()
    assert [item["id"] for item in listed] == [body["id"]]
    database.dispose()


def test_worker_job_ingests_uploaded_file_to_ready(tmp_path):
    client, database, store, worker = make_client(tmp_path)
    alice = auth_headers(client, "alice")

    body = upload_file(client, alice, filename="notes.md", content="文件正文：幂等操作会避免重复写入。").json()
    assert body["state"] == "pending"

    assert worker.run_next_job() is True

    document = client.get(f"/documents/{body['id']}", headers=alice).json()
    assert document["state"] == "ready"
    assert store.inserted[0][1] == "notes.md"

    again = upload_file(client, alice, filename="notes.md", content="文件正文：幂等操作会避免重复写入。")
    assert again.status_code == 201
    assert again.json()["id"] == body["id"]
    assert again.json()["state"] == "ready"
    assert len(store.inserted) == 1
    assert worker.run_next_job() is False
    database.dispose()


def test_worker_marks_failed_on_ingest_error_and_keeps_processing_queue(tmp_path):
    model = FakeModel()
    client, database, store, worker = make_client(tmp_path, model=model)
    alice = auth_headers(client, "alice")

    good = upload_file(client, alice, filename="good.md", content="正常内容").json()
    assert worker.run_next_job() is True
    assert client.get(f"/documents/{good['id']}", headers=alice).json()["state"] == "ready"

    model.fail = True
    broken = upload_file(client, alice, filename="broken.md", content="会失败的内容").json()
    assert worker.run_next_job() is True
    failed = client.get(f"/documents/{broken['id']}", headers=alice).json()
    assert failed["state"] == "failed"
    assert "模型爆炸" in failed["error_message"]

    model.fail = False
    next_one = upload_file(client, alice, filename="next.md", content="后续内容").json()
    assert worker.run_next_job() is True
    assert client.get(f"/documents/{next_one['id']}", headers=alice).json()["state"] == "ready"
    assert len(store.inserted) == 2
    database.dispose()


def test_duplicate_upload_while_processing_returns_409(tmp_path):
    client, database, store, _ = make_client(tmp_path)
    alice = auth_headers(client, "alice")

    first = upload_file(client, alice, filename="notes.md", content="第一份内容")
    assert first.status_code == 201
    assert first.json()["state"] == "pending"

    same = upload_file(client, alice, filename="notes.md", content="第一份内容")
    assert same.status_code == 409
    assert "处理" in same.json()["detail"]

    different = upload_file(client, alice, filename="notes.md", content="完全不同的内容")
    assert different.status_code == 409
    assert "冲突" in different.json()["detail"]

    assert store.inserted == []
    database.dispose()


def test_retry_failed_document_reenqueues_and_recovers(tmp_path):
    model = FakeModel()
    client, database, store, worker = make_client(tmp_path, model=model)
    alice = auth_headers(client, "alice")

    model.fail = True
    broken = upload_file(client, alice, filename="broken.md", content="会失败的内容").json()
    assert worker.run_next_job() is True
    assert client.get(f"/documents/{broken['id']}", headers=alice).json()["state"] == "failed"

    model.fail = False
    retried = client.post(f"/documents/{broken['id']}/retry", headers=alice)
    assert retried.status_code == 202
    assert retried.json()["id"] == broken["id"]

    assert worker.run_next_job() is True
    recovered = client.get(f"/documents/{broken['id']}", headers=alice).json()
    assert recovered["state"] == "ready"
    assert recovered["error_message"] is None
    assert len(store.inserted) == 1
    database.dispose()


def test_retry_rejects_non_failed_documents(tmp_path):
    client, database, store, worker = make_client(tmp_path)
    alice = auth_headers(client, "alice")

    pending = upload_file(client, alice, filename="pending.md", content="排队内容").json()
    assert client.post(f"/documents/{pending['id']}/retry", headers=alice).status_code == 409

    assert worker.run_next_job() is True
    ready = upload_file(client, alice, filename="ready.md", content="完成内容").json()
    assert worker.run_next_job() is True
    assert client.post(f"/documents/{ready['id']}/retry", headers=alice).status_code == 409

    assert client.post("/documents/nonexistent/retry", headers=alice).status_code == 404
    database.dispose()


def test_retry_without_sidecar_marks_failed_with_clear_message(tmp_path):
    model = FakeModel()
    client, database, store, worker = make_client(tmp_path, model=model)
    alice = auth_headers(client, "alice")

    model.fail = True
    broken = upload_file(client, alice, filename="broken.md", content="会失败的内容").json()
    assert worker.run_next_job() is True

    upload_dir = tmp_path / "uploads"
    (upload_dir / f"{broken['id']}.upload").unlink()
    (upload_dir / f"{broken['id']}.json").unlink()

    model.fail = False
    assert client.post(f"/documents/{broken['id']}/retry", headers=alice).status_code == 202
    assert worker.run_next_job() is True

    failed = client.get(f"/documents/{broken['id']}", headers=alice).json()
    assert failed["state"] == "failed"
    assert "重新上传" in failed["error_message"]
    database.dispose()


def test_nul_bytes_are_stripped_from_ingested_chunks(tmp_path):
    client, database, store, worker = make_client(tmp_path)
    alice = auth_headers(client, "alice")

    body = upload_file(
        client, alice, filename="weird.md", content="正常内容\x00后面还有内容"
    ).json()
    assert worker.run_next_job() is True

    document = client.get(f"/documents/{body['id']}", headers=alice).json()
    assert document["state"] == "ready"
    assert store.inserted
    assert all("\x00" not in text for _, _, text, _ in store.inserted)
    database.dispose()


def test_recover_interrupted_marks_stale_processing_failed(tmp_path):
    url = f"sqlite+pysqlite:///{(tmp_path / 'recover.db').as_posix()}"
    upgrade_database(url)
    database = Database(url)
    lifecycle = DocumentLifecycle(database.sessions)

    stuck = lifecycle.start_upload(owner_id=1, source="stuck.md", version="v1", content="卡住的内容")
    lifecycle.mark_processing(stuck.id)

    healthy = lifecycle.start_upload(owner_id=1, source="healthy.md", version="v1", content="正常内容")
    lifecycle.mark_processing(healthy.id)
    lifecycle.activate(healthy.id)

    recovered = lifecycle.recover_interrupted()

    assert recovered == 1
    assert lifecycle.get(stuck.id).state == "failed"
    assert "中断" in lifecycle.get(stuck.id).error_message
    assert lifecycle.get(healthy.id).state == "ready"
    database.dispose()
