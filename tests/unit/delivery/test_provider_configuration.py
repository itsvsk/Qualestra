from __future__ import annotations

from collections.abc import Mapping

import httpx
import pytest
from pydantic import ValidationError

from sentinelqa.delivery import DeliveryRegistry, build_delivery_registry_from_env
from sentinelqa.delivery.configuration import (
    DeliveryProviderConfiguration,
    load_delivery_provider_configuration,
    register_provider_destinations,
)
from sentinelqa.delivery.providers import ProviderHttpResponse


class OfflineTicketTransport:
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, object] | None = None,
    ) -> ProviderHttpResponse:
        del method, url, headers, json_body
        raise AssertionError("configuration tests must not perform provider requests")


def test_example_configuration_registers_only_safe_catalog_metadata() -> None:
    configuration = load_delivery_provider_configuration("config/delivery-providers.example.yaml")
    registry = DeliveryRegistry()

    register_provider_destinations(
        registry,
        configuration,
        environment={},
        ticket_transport=OfflineTicketTransport(),
        slack_transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(
                AssertionError(f"unexpected provider request: {request.url}")
            )
        ),
    )

    assert [(item.provider, item.kind) for item in registry.catalog()] == [
        ("github", "ticket"),
        ("jira", "ticket"),
        ("slack", "alert"),
    ]
    serialized = repr(registry.catalog()).lower()
    assert "api_token_env" not in serialized
    assert "token_env" not in serialized
    assert "base_url" not in serialized
    assert "https://" not in serialized


@pytest.mark.parametrize(
    "destination",
    [
        {
            "provider": "github",
            "destination_id": "release",
            "display_name": "Release",
            "repository": "example/project",
            "token_env": "SENTINEL_GITHUB_TOKEN",
            "token": "request-provided-value",
        },
        {
            "provider": "jira",
            "destination_id": "release",
            "display_name": "Release",
            "base_url": "http://jira.example.test",
            "project_key": "QA",
            "api_token_env": "SENTINEL_JIRA_TOKEN",
        },
        {
            "provider": "slack",
            "destination_id": "release",
            "display_name": "env:SENTINEL_SLACK_TOKEN",
            "channel": "C0123456789",
            "token_env": "SENTINEL_SLACK_TOKEN",
        },
    ],
)
def test_provider_configuration_rejects_secrets_unsafe_origins_and_sensitive_metadata(
    destination: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        DeliveryProviderConfiguration.model_validate(
            {"schema_version": "1.0", "destinations": [destination]}
        )


def test_registry_loads_configured_provider_catalog_from_server_environment(
    monkeypatch, tmp_path
) -> None:
    provider_file = tmp_path / "providers.yaml"
    provider_file.write_text(
        """
schema_version: "1.0"
destinations:
  - provider: slack
    destination_id: release-alerts
    display_name: Release alerts
    channel: C0123456789
    token_env: SENTINEL_SLACK_TOKEN
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("SENTINEL_DELIVERY_CONFIG", str(provider_file))

    catalog = build_delivery_registry_from_env().catalog()

    assert any(
        item.connector_id == "slack"
        and item.destination_id == "release-alerts"
        and item.kind == "alert"
        for item in catalog
    )
