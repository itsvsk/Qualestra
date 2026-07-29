import json

from fastapi.testclient import TestClient

from apps.control_api.main import app

client = TestClient(app)


def test_destination_catalog_exposes_only_non_secret_metadata() -> None:
    response = client.get("/v1/delivery-destinations")

    assert response.status_code == 200
    items = response.json()["items"]
    assert items
    assert all(
        set(item)
        == {
            "connector_id",
            "destination_id",
            "display_name",
            "kind",
            "provider",
            "supported_modes",
        }
        for item in items
    )
    assert {item["kind"] for item in items} <= {"artifact", "ticket", "alert"}
    assert all(set(item["supported_modes"]) <= {"dry_run", "manual", "automatic"} for item in items)

    serialized = json.dumps(items).lower()
    for forbidden in ("credential", "token", "password", "webhook", "base_url"):
        assert forbidden not in serialized
