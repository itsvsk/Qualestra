from fastapi.testclient import TestClient

from apps.control_api.main import app

client = TestClient(app)


def test_create_read_and_cancel_run():
    response = client.post(
        "/v1/runs", json={"target_id": "atlas-local", "trigger": "manual", "seed": 41021}
    )
    assert response.status_code == 202
    run_id = response.json()["id"]
    summary = client.get(f"/v1/runs/{run_id}")
    assert summary.status_code == 200
    assert summary.json()["task_count"] == 1
    events = client.get(f"/v1/runs/{run_id}/events").json()["items"]
    assert events[0]["event_type"] == "run.created"
    cancelled = client.post(f"/v1/runs/{run_id}/cancel")
    assert cancelled.json()["status"] == "cancelled"


def test_create_rejects_unknown_target_and_persona():
    unknown_target = client.post("/v1/runs", json={"target_id": "not-configured"})
    assert unknown_target.status_code == 422
    unknown_persona = client.post(
        "/v1/runs", json={"target_id": "atlas-local", "personas": ["intruder"]}
    )
    assert unknown_persona.status_code == 422
