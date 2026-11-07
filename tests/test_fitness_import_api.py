from fastapi.testclient import TestClient
from fastapi.exceptions import ResponseValidationError
import pytest
from fitness.service import FitnessService
from sqlalchemy import create_engine, select

from storage.migration import upgrade_database
from storage.models import ExerciseSet
from web.api import create_app


CSV = "workout_date,exercise_name,weight_kg,reps,recorded_on\n2026-10-18,卧推,60,5,\n2026-10-18,卧推,50,8,\n"


def client_for(tmp_path):
    database = tmp_path / "fitness-import.db"
    upgrade_database(f"sqlite+pysqlite:///{database.as_posix()}")
    client = TestClient(create_app(database, token_secret="test-only-secret-key-at-least-32-bytes"))
    client.post("/auth/register", json={"username": "import_user", "password": "test-password-123"})
    token = client.post("/auth/login", data={"username": "import_user", "password": "test-password-123"}).json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_csv_preview_validates_without_writing(tmp_path):
    client = client_for(tmp_path)
    preview = client.post("/fitness/import/preview", json={"csv_text": CSV})
    assert preview.status_code == 200
    assert preview.json()["row_count"] == 2
    assert client.get("/fitness/progress").json()["volume_by_exercise"] == {}


def test_csv_commit_is_idempotent(tmp_path):
    client = client_for(tmp_path)
    first = client.post("/fitness/import/commit", json={"csv_text": CSV})
    second = client.post("/fitness/import/commit", json={"csv_text": CSV})
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    assert client.get("/fitness/progress").json()["volume_by_exercise"]["卧推"] == 700


def test_csv_commit_validates_the_declared_response_model(tmp_path, monkeypatch):
    client = client_for(tmp_path)

    monkeypatch.setattr(
        FitnessService,
        "commit_import",
        lambda *_: {"row_count": 1, "workout_count": 1, "created": True},
    )

    with pytest.raises(ResponseValidationError):
        client.post("/fitness/import/commit", json={"csv_text": CSV})


def test_csv_preview_rejects_invalid_rows(tmp_path):
    client = client_for(tmp_path)
    response = client.post(
        "/fitness/import/preview",
        json={"csv_text": "workout_date,exercise_name,weight_kg,reps\n2026-02-30,卧推,60,5\n"},
    )
    assert response.status_code == 422


def test_csv_preview_accepts_optional_body_part_column(tmp_path):
    client = client_for(tmp_path)
    csv_text = (
        "workout_date,exercise_name,weight_kg,reps,body_part,recorded_on\n"
        "2026-10-18,卧推,60,5,胸,\n"
        "2026-10-18,引体向上,10,8,背,\n"
    )
    response = client.post("/fitness/import/preview", json={"csv_text": csv_text})

    assert response.status_code == 200
    rows = response.json()["rows"]
    assert [row["body_part"] for row in rows] == ["胸", "背"]


def test_csv_preview_treats_empty_body_part_as_none(tmp_path):
    client = client_for(tmp_path)
    csv_text = (
        "workout_date,exercise_name,weight_kg,reps,body_part,recorded_on\n"
        "2026-10-18,跑步,60,30,,\n"
    )
    response = client.post("/fitness/import/preview", json={"csv_text": csv_text})

    assert response.status_code == 200
    assert response.json()["rows"][0]["body_part"] is None


def test_csv_preview_without_body_part_column_keeps_old_shape(tmp_path):
    client = client_for(tmp_path)
    response = client.post("/fitness/import/preview", json={"csv_text": CSV})

    assert response.status_code == 200
    assert "body_part" not in response.json()["rows"][0]


def test_csv_commit_persists_body_part_on_exercise_sets(tmp_path):
    client = client_for(tmp_path)
    csv_text = (
        "workout_date,exercise_name,weight_kg,reps,body_part,recorded_on\n"
        "2026-10-18,卧推,60,5,胸,\n"
        "2026-10-18,引体向上,10,8,背,\n"
    )
    response = client.post("/fitness/import/commit", json={"csv_text": csv_text})
    assert response.status_code == 201

    engine = create_engine(f"sqlite+pysqlite:///{(tmp_path / 'fitness-import.db').as_posix()}")
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                select(ExerciseSet.exercise_name, ExerciseSet.body_part).order_by(ExerciseSet.id)
            ).all()
        assert rows == [("卧推", "胸"), ("引体向上", "背")]
    finally:
        engine.dispose()


def test_csv_rejects_non_finite_weight_and_ambiguous_date(tmp_path):
    client = client_for(tmp_path)
    for csv_text in (
        "workout_date,exercise_name,weight_kg,reps,recorded_on\n2026-10-18,卧推,inf,5,\n",
        "workout_date,exercise_name,weight_kg,reps,recorded_on\n2026-10-18,卧推,60,5,2026-10-18\n",
    ):
        assert client.post("/fitness/import/preview", json={"csv_text": csv_text}).status_code == 422
