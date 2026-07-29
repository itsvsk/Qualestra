import pytest

from sentinelqa.agents.change_impact import charter_for_files
from sentinelqa.config import load_yaml
from sentinelqa.domain.models import BrowserSettings, RunRequest


def test_change_impact_combines_files_openapi_history_and_coverage() -> None:
    charter = charter_for_files(
        ["services/billing/subscription.py"],
        configuration=load_yaml("config/impact-map.yaml"),
        changed_operations=["PATCH /organizations/{id}"],
        risk_history={"billing": 0.95},
        coverage_gaps=["checkout"],
    )
    direct = {item["feature"]: item for item in charter["impacts"]}
    assert direct["billing"]["risk_score"] == 0.95
    assert direct["tenant"]["classification"] == "direct"
    assert charter["coverage_gaps"] == ["checkout"]
    assert "authorization" in charter["adjacent_features"]


def test_browser_matrix_is_validated_before_a_run() -> None:
    request = RunRequest(
        target_id="example",
        browsers=("chromium", "firefox", "webkit"),
        browser_settings=BrowserSettings(locale="fr-FR", timezone_id="Europe/Paris"),
    )
    assert request.browsers == ("chromium", "firefox", "webkit")
    with pytest.raises(ValueError):
        RunRequest(target_id="example", browsers=())
    with pytest.raises(ValueError):
        RunRequest(target_id="example", browsers=("chromium", "chromium"))
