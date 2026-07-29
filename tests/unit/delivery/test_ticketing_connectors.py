from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import httpx
import pytest

from sentinelqa.delivery.connectors import DeliveryConnectorError
from sentinelqa.delivery.providers import (
    GitHubIssuesConnector,
    GitHubIssuesDestination,
    HttpxAsyncTransport,
    JiraConnector,
    JiraDestination,
    ProviderHttpResponse,
    build_ticket_payload,
)
from sentinelqa.delivery.providers.http import ProviderResponseTooLarge
from sentinelqa.domain.models import RunReport, stable_digest

IDEMPOTENCY_KEY = "sha256:" + "a" * 64
RUN_ID = UUID("00000000-0000-4000-8000-000000000901")
REPORT_ID = UUID("00000000-0000-4000-8000-000000000902")
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


class FakeTransport:
    def __init__(self, *responses: ProviderHttpResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, object] | None = None,
    ) -> ProviderHttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json_body": json_body,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_report(
    *,
    target_id: str = "checkout",
    defect_title: str = "Checkout total is incorrect",
) -> RunReport:
    document = {
        "schema_version": "1.0",
        "report_type": "sentinelqa.run",
        "report_id": "sha256:" + "b" * 64,
        "generated_at": NOW.isoformat(),
        "technical_summary": {
            "run_id": str(RUN_ID),
            "target_id": target_id,
            "trigger": "pull_request",
            "commit_sha": "abc123",
            "run_status": "blocked",
            "started_at": NOW.isoformat(),
            "ended_at": NOW.isoformat(),
            "duration_seconds": 0,
            "verified_defect_count": 1,
            "severity_counts": {"critical": 1, "high": 0, "medium": 0, "low": 0},
            "affected_areas": ["checkout"],
            "suspected_components": ["totals-api"],
            "personas": ["buyer"],
            "evidence_reference_count": 1,
        },
        "executive_summary": {
            "headline": "Release blocked",
            "narrative": "One verified critical defect.",
            "key_findings": ["Checkout total is incorrect"],
        },
        "release_summary": {
            "run_id": str(RUN_ID),
            "outcome": "BLOCK",
            "release_confidence": 0.91,
            "blockers": ["verified_critical_defects"],
            "matched_warnings": [],
            "metrics": {"verified_critical_defects": 1},
            "rules": [],
            "generated_at": NOW.isoformat(),
            "narrative": "Release blocked.",
        },
        "defects": [
            {
                "defect_id": "00000000-0000-4000-8000-000000000903",
                "title": defect_title,
                "severity": "critical",
                "confidence": 0.95,
                "status": "verified",
                "affected_area": "checkout",
                "signature": "checkout-total",
                "persona": "buyer",
                "suspected_component": "totals-api",
                "preconditions": [],
                "steps_to_reproduce": [],
                "expected": "Correct total",
                "actual": "Incorrect total",
                "verification": {"attempts": 2, "matched": 2},
                "evidence": [{"kind": "trace", "artifact_id": "sha256:evidence"}],
                "suggested_investigation": "Inspect totals.",
                "created_at": NOW.isoformat(),
            }
        ],
        "evidence_references": [
            {
                "defect_id": "00000000-0000-4000-8000-000000000903",
                "kind": "trace",
                "artifact_id": "sha256:evidence",
            }
        ],
    }
    content = json.dumps(document, sort_keys=True)
    return RunReport(
        report_id=REPORT_ID,
        run_id=RUN_ID,
        kind="technical",
        format="json",
        content=content,
        digest=stable_digest(content),
        source_fingerprint="sha256:" + "c" * 64,
        generated_at=NOW,
    )


def test_ticket_payload_is_bounded_structured_and_neutralizes_active_text() -> None:
    report = make_report(
        target_id="@release https://untrusted.example/" + "x" * 400,
        defect_title="@team inspect https://untrusted.example/image",
    )

    payload = build_ticket_payload(report, IDEMPOTENCY_KEY, base_labels=("qa",))

    assert len(payload.title) <= 200
    assert len(payload.body) <= 16_000
    assert "@release" not in payload.title
    assert "https://" not in payload.title
    assert "@team" not in payload.body
    assert "https://" not in payload.body
    assert report.content not in payload.body
    assert payload.idempotency_marker in payload.body
    assert payload.labels == ("qa", "sentinelqa", "gate-block", "severity-critical")


async def test_github_creates_bounded_issue_and_uses_only_static_target() -> None:
    transport = FakeTransport(
        ProviderHttpResponse(200, {"total_count": 0, "items": []}),
        ProviderHttpResponse(
            201,
            {
                "number": 42,
                "node_id": "I_kwSAFE",
                "html_url": "https://untrusted.invalid/credential?token=secret",
            },
        ),
    )
    destination = GitHubIssuesDestination(
        destination_id="release-repo",
        repository="acme/shop",
        token_env="GITHUB_RELEASE_TOKEN",  # noqa: S106 - environment reference, not secret
        labels=("qa",),
    )
    connector = GitHubIssuesConnector(
        {"release-repo": destination},
        transport,
        environ={"GITHUB_RELEASE_TOKEN": "very-secret"},
    )

    receipt = await connector.deliver(
        make_report(),
        destination_id="release-repo",
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert receipt.external_id == "I_kwSAFE"
    assert receipt.external_url == "https://github.com/acme/shop/issues/42"
    assert [call["method"] for call in transport.calls] == ["GET", "POST"]
    assert cast(str, transport.calls[0]["url"]).startswith("https://api.github.com/search/issues?")
    assert transport.calls[1]["url"] == "https://api.github.com/repos/acme/shop/issues"
    create_body = cast(dict[str, object], transport.calls[1]["json_body"])
    assert create_body["labels"] == ["qa", "sentinelqa", "gate-block", "severity-critical"]
    assert IDEMPOTENCY_KEY in cast(str, create_body["body"])
    assert "very-secret" not in json.dumps(create_body)


async def test_github_retry_finds_provider_side_duplicate_after_unknown_failure() -> None:
    marker = f"<!-- sentinelqa-idempotency: {IDEMPOTENCY_KEY} -->"
    transport = FakeTransport(
        ProviderHttpResponse(200, {"total_count": 0, "items": []}),
        RuntimeError("Authorization: Bearer very-secret"),
        ProviderHttpResponse(
            200,
            {
                "total_count": 1,
                "items": [{"number": 9, "node_id": "I_existing", "body": marker}],
            },
        ),
    )
    connector = GitHubIssuesConnector(
        {
            "release-repo": GitHubIssuesDestination(
                destination_id="release-repo",
                repository="acme/shop",
                token_env="GITHUB_RELEASE_TOKEN",  # noqa: S106
            )
        },
        transport,
        environ={"GITHUB_RELEASE_TOKEN": "very-secret"},
    )

    with pytest.raises(DeliveryConnectorError) as first_error:
        await connector.deliver(
            make_report(),
            destination_id="release-repo",
            idempotency_key=IDEMPOTENCY_KEY,
        )
    receipt = await connector.deliver(
        make_report(),
        destination_id="release-repo",
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert first_error.value.metadata.retryable
    assert str(first_error.value) == "github.transport_failure"
    assert "very-secret" not in str(first_error.value)
    assert receipt.external_id == "I_existing"
    assert [call["method"] for call in transport.calls] == ["GET", "POST", "GET"]


async def test_jira_create_then_deduplicate_uses_static_project_and_adf_text() -> None:
    transport = FakeTransport(
        ProviderHttpResponse(200, {"issues": []}),
        ProviderHttpResponse(201, {"id": "10001", "key": "SHOP-17"}),
        ProviderHttpResponse(200, {"issues": [{"id": "10001", "key": "SHOP-17"}]}),
    )
    connector = JiraConnector(
        {
            "release-project": JiraDestination(
                destination_id="release-project",
                base_url="https://acme.atlassian.net",
                project_key="SHOP",
                api_token_env="JIRA_RELEASE_TOKEN",  # noqa: S106
                user_email_env="JIRA_RELEASE_USER",
                labels=("qa",),
            )
        },
        transport,
        environ={
            "JIRA_RELEASE_TOKEN": "very-secret",
            "JIRA_RELEASE_USER": "qa@example.test",
        },
    )

    first = await connector.deliver(
        make_report(),
        destination_id="release-project",
        idempotency_key=IDEMPOTENCY_KEY,
    )
    second = await connector.deliver(
        make_report(),
        destination_id="release-project",
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert first == second
    assert first.external_url == "https://acme.atlassian.net/browse/SHOP-17"
    assert [call["method"] for call in transport.calls] == ["POST", "POST", "POST"]
    search_body = cast(dict[str, object], transport.calls[0]["json_body"])
    assert 'project = "SHOP"' in cast(str, search_body["jql"])
    create_body = cast(dict[str, object], transport.calls[1]["json_body"])
    fields = cast(dict[str, object], create_body["fields"])
    assert cast(dict[str, str], fields["project"]) == {"key": "SHOP"}
    assert "sentinelqa-idem-" + "a" * 32 in cast(list[str], fields["labels"])
    assert IDEMPOTENCY_KEY in json.dumps(fields["description"])
    assert "very-secret" not in json.dumps(create_body)
    assert (
        sum(cast(str, call["url"]).endswith("/rest/api/3/issue") for call in transport.calls) == 1
    )


async def test_provider_errors_are_classified_without_response_or_secret_content() -> None:
    transport = FakeTransport(
        ProviderHttpResponse(
            429,
            {"error": "token=very-secret; internal=https://provider.invalid/private"},
        )
    )
    connector = GitHubIssuesConnector(
        {
            "release-repo": GitHubIssuesDestination(
                destination_id="release-repo",
                repository="acme/shop",
                token_env="GITHUB_RELEASE_TOKEN",  # noqa: S106
            )
        },
        transport,
        environ={"GITHUB_RELEASE_TOKEN": "very-secret"},
    )

    with pytest.raises(DeliveryConnectorError) as caught:
        await connector.deliver(
            make_report(),
            destination_id="release-repo",
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert caught.value.metadata.code == "github.temporarily_unavailable"
    assert caught.value.metadata.category == "transient"
    assert caught.value.metadata.retryable
    persisted = caught.value.metadata.model_dump_json()
    assert "very-secret" not in persisted
    assert "provider.invalid" not in persisted


def test_static_destination_configuration_rejects_dynamic_or_credentialed_targets() -> None:
    with pytest.raises(ValueError):
        GitHubIssuesDestination(
            destination_id="bad",
            repository="../../other",
            token_env="TOKEN",  # noqa: S106
        )
    with pytest.raises(ValueError):
        JiraDestination(
            destination_id="bad",
            base_url="https://user:secret@acme.atlassian.net",
            project_key="SHOP",
            api_token_env="TOKEN",  # noqa: S106
        )
    with pytest.raises(ValueError):
        JiraDestination(
            destination_id="bad",
            base_url="https://acme.atlassian.net",
            project_key='SHOP" OR project = "OTHER',
            api_token_env="TOKEN",  # noqa: S106
        )


async def test_httpx_transport_rejects_declared_and_streamed_oversized_responses() -> None:
    async def declared_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "500"}, content=b"{}")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(declared_handler),
        follow_redirects=False,
    ) as client:
        transport = HttpxAsyncTransport(client, max_response_bytes=100)
        with pytest.raises(ProviderResponseTooLarge):
            await transport.request("GET", "https://provider.example/test", headers={})

    async def streamed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=httpx.ByteStream(b"x" * 101))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(streamed_handler),
        follow_redirects=False,
    ) as client:
        transport = HttpxAsyncTransport(client, max_response_bytes=100)
        with pytest.raises(ProviderResponseTooLarge):
            await transport.request("GET", "https://provider.example/test", headers={})
