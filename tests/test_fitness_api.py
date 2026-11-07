from datetime import date

from fastapi.testclient import TestClient

from storage.migration import upgrade_database
from web.api import create_app


def client_for(tmp_path):
    database = tmp_path / "fitness.db"
    upgrade_database(f"sqlite+pysqlite:///{database.as_posix()}")
    client = TestClient(create_app(database, token_secret="test-only-secret-key-at-least-32-bytes"))
    client.post("/auth/register", json={"username": "fitness_user", "password": "test-password-123"})
    token = client.post("/auth/login", data={"username": "fitness_user", "password": "test-password-123"}).json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})
    return client


def test_create_workout_and_progress_stats(tmp_path):
    client = client_for(tmp_path)
    response = client.post("/fitness/workouts", json={
        "workout_date": "2026-10-18",
        "sets": [
            {"exercise_name": "卧推", "weight_kg": 60, "reps": 5},
            {"exercise_name": "卧推", "weight_kg": 50, "reps": 8},
        ],
    })
    assert response.status_code == 201
    assert response.json()["volume"] == 700

    progress = client.get("/fitness/progress")
    assert progress.status_code == 200
    assert progress.json()["exercise_pr"]["卧推"] == 60
    assert progress.json()["volume_by_exercise"]["卧推"] == 700


def test_progress_returns_volume_timeline_matrix_by_date(tmp_path):
    client = client_for(tmp_path)
    client.post("/fitness/workouts", json={
        "workout_date": "2026-10-18",
        "sets": [
            {"exercise_name": "卧推", "weight_kg": 60, "reps": 5},
            {"exercise_name": "深蹲", "weight_kg": 80, "reps": 10},
        ],
    })
    client.post("/fitness/workouts", json={
        "workout_date": "2026-10-19",
        "sets": [{"exercise_name": "卧推", "weight_kg": 50, "reps": 8}],
    })

    timeline = client.get("/fitness/progress").json()["volume_timeline"]

    assert timeline["dates"] == ["2026-10-18", "2026-10-19"]
    assert timeline["exercises"]["卧推"] == [300.0, 400.0]
    assert timeline["exercises"]["深蹲"] == [800.0, 0.0]


def test_progress_volume_timeline_is_empty_without_workouts(tmp_path):
    client = client_for(tmp_path)
    timeline = client.get("/fitness/progress").json()["volume_timeline"]
    assert timeline == {"dates": [], "exercises": {}}


def test_body_weight_weekly_average_is_owner_scoped(tmp_path):
    client = client_for(tmp_path)
    assert client.post("/fitness/weight", json={"recorded_on": "2026-10-13", "weight_kg": 70}).status_code == 201
    assert client.post("/fitness/weight", json={"recorded_on": "2026-10-15", "weight_kg": 71}).status_code == 201
    response = client.get("/fitness/progress")
    assert response.json()["weekly_weight"]["2026-W42"] == 70.5
