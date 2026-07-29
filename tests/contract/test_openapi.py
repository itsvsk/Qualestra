from apps.control_api.main import app as control_app
from atlas.api.main import app as atlas_app


def test_public_control_api_operations_exist():
    assert control_app.openapi()["info"]["version"] == "0.1.0"
    paths = control_app.openapi()["paths"]
    expected = {
        "/v1/runs",
        "/v1/runs/{run_id}",
        "/v1/runs/{run_id}/cancel",
        "/v1/runs/{run_id}/events",
        "/v1/runs/{run_id}/agents",
        "/v1/runs/{run_id}/tasks",
        "/v1/runs/{run_id}/coverage",
        "/v1/runs/{run_id}/defects",
        "/v1/defects/{defect_id}",
        "/v1/verification-runs",
        "/v1/gates/{run_id}",
        "/v1/runs/{run_id}/report",
        "/v1/runs/{run_id}/report/regenerate",
        "/v1/runs/{run_id}/deliveries",
        "/v1/deliveries/{delivery_id}/approve",
        "/v1/deliveries/{delivery_id}/retry",
        "/v1/artifacts/{artifact_id}",
    }
    assert expected.issubset(paths)


def test_atlas_openapi_is_v3_and_has_test_contract():
    document = atlas_app.openapi()
    assert document["openapi"].startswith("3.")
    assert {"/__test/reset", "/__test/seed", "/__test/state"}.issubset(document["paths"])
