from fastapi.testclient import TestClient

from atlas.api.main import app, state

client = TestClient(app)
CONTROL = {"x-test-control-token": "local-test-token"}


def setup_function():
    state.faults.clear()
    state.seed()


def test_reset_and_seed_are_deterministic(monkeypatch):
    monkeypatch.setenv("ATLAS_TEST_MODE", "true")
    monkeypatch.setenv("ATLAS_TEST_CONTROL_TOKEN", "local-test-token")
    client.post("/__test/reset", headers=CONTROL).raise_for_status()
    client.post("/__test/seed?seed=41021", headers=CONTROL).raise_for_status()
    first = client.get("/__test/state", headers=CONTROL).json()
    client.post("/__test/reset", headers=CONTROL).raise_for_status()
    client.post("/__test/seed?seed=41021", headers=CONTROL).raise_for_status()
    second = client.get("/__test/state", headers=CONTROL).json()
    assert first == second


def test_test_control_rejects_missing_token(monkeypatch):
    monkeypatch.setenv("ATLAS_TEST_MODE", "true")
    assert client.get("/__test/state").status_code == 403


def test_viewer_mutation_fault_switch():
    headers = {"x-atlas-persona": "viewer"}
    payload = {"title": "Unauthorized"}
    assert client.patch("/v1/work-items/item-a", headers=headers, json=payload).status_code == 403
    state.faults.add("F01")
    response = client.patch("/v1/work-items/item-a", headers=headers, json=payload)
    assert response.status_code == 200
    assert state.data["work_items"]["item-a"]["title"] == "Unauthorized"


def test_cross_tenant_read_fault_switch():
    headers = {"x-atlas-persona": "viewer"}
    assert client.get("/v1/work-items/item-b", headers=headers).status_code == 404
    state.faults.add("F02")
    assert client.get("/v1/work-items/item-b", headers=headers).status_code == 200


def test_all_seeded_fault_ids_can_be_enabled(monkeypatch):
    monkeypatch.setenv("ATLAS_TEST_MODE", "true")
    monkeypatch.setenv("ATLAS_TEST_CONTROL_TOKEN", "local-test-token")
    for index in range(1, 11):
        response = client.post(f"/__test/faults/F{index:02d}/enable", headers=CONTROL)
        assert response.status_code == 200
    assert state.faults == {f"F{index:02d}" for index in range(1, 11)}
