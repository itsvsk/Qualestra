from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest

from sentinelqa.delivery.connectors import DeliveryConnectorError
from sentinelqa.delivery.providers.slack import (
    SLACK_CHAT_POST_MESSAGE_ENDPOINT,
    SlackConnector,
    SlackDestination,
    build_slack_message,
)
from sentinelqa.domain.models import RunReport, stable_digest

RUN_ID = UUID("b2a834ce-7576-447a-bc52-f4acb1521aea")
IDEMPOTENCY_KEY = "sha256:" + "a" * 64
TOKEN = "xoxb-super-secret-token"  # noqa: S105 - inert credential-shaped test fixture


def _report(
    *,
    content: str | None = None,
    report_format: str = "json",
) -> RunReport:
    if content is None:
        content = json.dumps(
            {
                "technical_summary": {
                    "verified_defect_count": 3,
                },
                "executive_summary": {
                    "headline": "Checkout needs attention",
                    "narrative": "One critical finding blocks release.",
                },
                "release_summary": {
                    "outcome": "BLOCK",
                },
            }
        )
    return RunReport(
        run_id=RUN_ID,
        kind="technical",
        format=report_format,
        content=content,
        digest=stable_digest(content),
        source_fingerprint="sha256:" + "b" * 64,
        generated_at=datetime(2026, 7, 27, 10, 30, tzinfo=UTC),
    )


def _destination(**overrides: str) -> SlackDestination:
    values = {
        "destination_id": "release-alerts",
        "channel": "C0123456789",
        "token_env": "SENTINEL_SLACK_RELEASE_TOKEN",
    }
    values.update(overrides)
    return SlackDestination(**values)


async def test_posts_bounded_report_message_with_static_configuration() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"ok": True, "channel": "C0123456789", "ts": "1712345678.000100"},
        )

    connector = SlackConnector(
        (_destination(),),
        environment={"SENTINEL_SLACK_RELEASE_TOKEN": TOKEN},
        transport=httpx.MockTransport(handler),
    )
    report = _report()

    first = await connector.deliver(
        report,
        destination_id="release-alerts",
        idempotency_key=IDEMPOTENCY_KEY,
    )
    second = await connector.deliver(
        report,
        destination_id="release-alerts",
        idempotency_key=IDEMPOTENCY_KEY,
    )

    assert first.external_id == "1712345678.000100"
    assert first == second
    assert len(requests) == 2
    payloads = [json.loads(request.content) for request in requests]
    assert {request.url for request in requests} == {httpx.URL(SLACK_CHAT_POST_MESSAGE_ENDPOINT)}
    assert all(request.headers["authorization"] == f"Bearer {TOKEN}" for request in requests)
    assert payloads[0] == payloads[1]
    assert payloads[0]["channel"] == "C0123456789"
    assert payloads[0]["client_msg_id"] == "f882ea40-0ed4-5549-af2d-d17a164a82cf"
    assert payloads[0]["unfurl_links"] is False
    assert "BLOCK · 3 verified findings" in payloads[0]["text"]
    assert TOKEN not in json.dumps(payloads[0])
    assert len(payloads[0]["blocks"]) == 3


def test_message_is_bounded_redacted_and_never_triggers_user_supplied_mentions() -> None:
    unsafe = "# Run\n<!channel> Authorization: Bearer should-not-survive token=another-secret " + (
        "failure details " * 1_000
    )

    message = build_slack_message(_report(content=unsafe, report_format="markdown"))

    rendered = json.dumps(message.blocks)
    assert len(message.text) <= 2_400
    section = message.blocks[1]["text"]["text"]
    assert isinstance(section, str)
    assert len(section) <= 1_800
    assert "<!channel>" not in rendered
    assert "&lt;!channel&gt;" in rendered
    assert "should-not-survive" not in rendered
    assert "another-secret" not in rendered
    assert "[REDACTED]" in rendered
    assert "<!channel>" not in message.text
    assert "should-not-survive" not in message.text
    assert "another-secret" not in message.text


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"destination_id": "../escape"}, "destination_id"),
        ({"channel": "#release-alerts"}, "channel"),
        ({"token_env": "raw-token-value"}, "token_env"),
        ({"endpoint": "http://slack.test/api/chat.postMessage"}, "HTTPS"),
        (
            {"endpoint": "https://token@slack.test/api/chat.postMessage"},
            "credentials",
        ),
        (
            {"endpoint": "https://slack.test/api/chat.postMessage?token=secret"},
            "query",
        ),
    ],
)
def test_rejects_unsafe_static_configuration(
    overrides: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _destination(**overrides)


async def test_configuration_failure_never_discloses_environment_values() -> None:
    connector = SlackConnector((_destination(),), environment={"UNRELATED": TOKEN})

    with pytest.raises(DeliveryConnectorError) as caught:
        await connector.deliver(
            _report(),
            destination_id="release-alerts",
            idempotency_key=IDEMPOTENCY_KEY,
        )

    error = caught.value
    assert error.metadata.code == "slack.credential_unavailable"
    assert error.metadata.category == "configuration"
    assert error.metadata.retryable is False
    assert TOKEN not in str(error)
    assert TOKEN not in error.metadata.redacted_message


@pytest.mark.parametrize(
    ("status_code", "body", "expected_code", "expected_category", "retryable"),
    [
        (
            429,
            {"ok": False, "error": "secret-provider-body"},
            "slack.rate_limited",
            "transient",
            True,
        ),
        (
            503,
            {"ok": False, "error": "secret-provider-body"},
            "slack.unavailable",
            "transient",
            True,
        ),
        (
            200,
            {"ok": False, "error": "invalid_auth", "detail": TOKEN},
            "slack.authentication_rejected",
            "configuration",
            False,
        ),
        (
            200,
            {"ok": False, "error": "channel_not_found", "detail": TOKEN},
            "slack.destination_rejected",
            "configuration",
            False,
        ),
        (
            200,
            {"ok": False, "error": "unknown_error", "detail": TOKEN},
            "slack.provider_rejected",
            "permanent",
            False,
        ),
    ],
)
async def test_provider_failures_have_only_safe_persistable_metadata(
    status_code: int,
    body: dict[str, Any],
    expected_code: str,
    expected_category: str,
    retryable: bool,
) -> None:
    connector = SlackConnector(
        (_destination(),),
        environment={"SENTINEL_SLACK_RELEASE_TOKEN": TOKEN},
        transport=httpx.MockTransport(lambda request: httpx.Response(status_code, json=body)),
    )

    with pytest.raises(DeliveryConnectorError) as caught:
        await connector.deliver(
            _report(),
            destination_id="release-alerts",
            idempotency_key=IDEMPOTENCY_KEY,
        )

    error = caught.value
    assert error.metadata.code == expected_code
    assert error.metadata.category == expected_category
    assert error.metadata.retryable is retryable
    persisted = error.metadata.model_dump_json()
    assert TOKEN not in persisted
    assert "secret-provider-body" not in persisted


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>provider secret</html>"),
        httpx.Response(200, json={"ok": True}),
        httpx.Response(200, json={"ok": True, "ts": "invalid timestamp"}),
    ],
)
async def test_malformed_success_response_is_retryable_and_redacted(
    response: httpx.Response,
) -> None:
    connector = SlackConnector(
        (_destination(),),
        environment={"SENTINEL_SLACK_RELEASE_TOKEN": TOKEN},
        transport=httpx.MockTransport(lambda request: response),
    )

    with pytest.raises(DeliveryConnectorError) as caught:
        await connector.deliver(
            _report(),
            destination_id="release-alerts",
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert caught.value.metadata.code == "slack.invalid_response"
    assert caught.value.metadata.category == "transient"
    assert caught.value.metadata.retryable is True
    assert "provider secret" not in caught.value.metadata.model_dump_json()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(
            200,
            content=b"x" * 64_001,
            headers={"content-type": "application/json"},
        ),
        httpx.Response(
            200,
            json={"ok": True, "ts": "1712345678.000100"},
            headers={"content-length": "64001"},
        ),
    ],
)
async def test_rejects_oversized_provider_responses_without_parsing(
    response: httpx.Response,
) -> None:
    connector = SlackConnector(
        (_destination(),),
        environment={"SENTINEL_SLACK_RELEASE_TOKEN": TOKEN},
        transport=httpx.MockTransport(lambda request: response),
    )

    with pytest.raises(DeliveryConnectorError) as caught:
        await connector.deliver(
            _report(),
            destination_id="release-alerts",
            idempotency_key=IDEMPOTENCY_KEY,
        )

    assert caught.value.metadata.code == "slack.invalid_response"
    assert caught.value.metadata.retryable is True
    assert len(caught.value.metadata.model_dump_json()) < 500
