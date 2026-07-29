from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx

from sentinelqa.delivery.connectors import (
    DeliveryConnectorError,
    DeliveryReceipt,
)
from sentinelqa.domain.models import RunReport
from sentinelqa.reporting.redaction import redact_report_text

SLACK_CHAT_POST_MESSAGE_ENDPOINT = "https://slack.com/api/chat.postMessage"
_DESTINATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_CHANNEL_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,79}$")
_ENV_REFERENCE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,126}$")
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_MESSAGE_SCAN_LIMIT = 32_000
_SUMMARY_LIMIT = 1_800
_FALLBACK_TEXT_LIMIT = 2_400
_RESPONSE_BODY_LIMIT = 64_000
_AUTH_CONFIGURATION_ERRORS = frozenset(
    {
        "account_inactive",
        "invalid_auth",
        "not_authed",
        "token_expired",
        "token_revoked",
    }
)
_DESTINATION_CONFIGURATION_ERRORS = frozenset(
    {
        "channel_not_found",
        "is_archived",
        "no_permission",
        "not_in_channel",
        "restricted_action",
    }
)
_TRANSIENT_PROVIDER_ERRORS = frozenset(
    {
        "fatal_error",
        "internal_error",
        "request_timeout",
        "service_unavailable",
    }
)
DeliveryErrorCategory = Literal[
    "transient",
    "permanent",
    "policy",
    "configuration",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class SlackDestination:
    """A static, server-owned Slack destination.

    ``token_env`` names an environment variable. It deliberately does not hold
    the credential itself, and delivery requests can select only
    ``destination_id``.
    """

    destination_id: str
    channel: str
    token_env: str
    endpoint: str = SLACK_CHAT_POST_MESSAGE_ENDPOINT

    def __post_init__(self) -> None:
        if not _DESTINATION_PATTERN.fullmatch(self.destination_id):
            raise ValueError("Slack destination_id is invalid")
        if not _CHANNEL_PATTERN.fullmatch(self.channel):
            raise ValueError("Slack channel must be a configured channel identifier")
        if not _ENV_REFERENCE_PATTERN.fullmatch(self.token_env):
            raise ValueError("Slack token_env must be an uppercase environment reference")
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Slack endpoint must be HTTPS without credentials, query data, or fragments"
            )


@dataclass(frozen=True, slots=True)
class SlackMessage:
    """Bounded Slack message derived solely from an immutable report."""

    text: str
    blocks: tuple[dict[str, Any], ...]


class SlackConnector:
    """Deliver immutable reports with Slack Web API ``chat.postMessage``.

    The optional HTTP transport exists for deterministic offline tests. The
    connector always owns the HTTP client, disables redirects, and never places
    credentials in a URL, payload, receipt, or exception.
    """

    connector_id = "slack"

    def __init__(
        self,
        destinations: tuple[SlackDestination, ...],
        *,
        environment: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not destinations:
            raise ValueError("Slack connector requires at least one configured destination")
        if not 0 < timeout_seconds <= 60:
            raise ValueError("Slack timeout must be between 0 and 60 seconds")
        resolved: dict[str, SlackDestination] = {}
        for destination in destinations:
            if destination.destination_id in resolved:
                raise ValueError("Slack destination identifiers must be unique")
            resolved[destination.destination_id] = destination
        self.destinations = destinations
        self._destinations = resolved
        self._environment = os.environ if environment is None else environment
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    async def deliver(
        self,
        report: RunReport,
        *,
        destination_id: str,
        idempotency_key: str,
    ) -> DeliveryReceipt:
        destination = self._destinations.get(destination_id)
        if destination is None:
            raise _safe_error(
                code="slack.destination_not_configured",
                category="configuration",
                retryable=False,
                message="The selected Slack destination is not configured.",
            )
        token = self._environment.get(destination.token_env)
        if not token or not token.strip():
            raise _safe_error(
                code="slack.credential_unavailable",
                category="configuration",
                retryable=False,
                message="The configured Slack credential is unavailable.",
            )
        if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
            raise _safe_error(
                code="slack.invalid_idempotency_key",
                category="configuration",
                retryable=False,
                message="The Slack delivery idempotency marker is invalid.",
            )

        message = build_slack_message(report)
        client_message_id = str(uuid5(NAMESPACE_URL, f"sentinelqa:slack:{idempotency_key}"))
        payload: dict[str, Any] = {
            "channel": destination.channel,
            "text": message.text,
            "blocks": list(message.blocks),
            "client_msg_id": client_message_id,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout_seconds,
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    destination.endpoint,
                    headers=headers,
                    json=payload,
                ) as response:
                    _raise_for_http_status(response.status_code)
                    _validate_response_size_header(response)
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > _RESPONSE_BODY_LIMIT:
                            raise _invalid_response_error()
                    bounded_response = httpx.Response(
                        response.status_code,
                        headers=response.headers,
                        content=bytes(body),
                        request=response.request,
                    )
        except httpx.TimeoutException as error:
            raise _safe_error(
                code="slack.timeout",
                category="transient",
                retryable=True,
                message="Slack did not respond before the configured timeout.",
            ) from error
        except httpx.RequestError as error:
            raise _safe_error(
                code="slack.transport_failure",
                category="transient",
                retryable=True,
                message="Slack could not be reached by the configured transport.",
            ) from error

        data = _validated_response(bounded_response)
        timestamp = data["ts"]
        return DeliveryReceipt(external_id=timestamp)


def build_slack_message(report: RunReport) -> SlackMessage:
    """Build a stable, bounded alert without accepting templates or actions."""

    summary = _report_summary(report)
    redacted_summary = redact_report_text(summary)
    safe_summary = _truncate(_slack_escape(redacted_summary), _SUMMARY_LIMIT)
    heading = f"Qualestra report · {report.run_id}"
    context = (
        f"Format: {report.format} · Digest: "
        f"{report.digest.removeprefix('sha256:')[:12]} · "
        f"Generated: {report.generated_at.isoformat()}"
    )
    fallback = _truncate(
        f"{heading}. {_slack_escape(redacted_summary)}",
        _FALLBACK_TEXT_LIMIT,
    )
    blocks: tuple[dict[str, Any], ...] = (
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": _truncate(heading, 150),
                "emoji": False,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": safe_summary,
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": _slack_escape(context),
                }
            ],
        },
    )
    return SlackMessage(text=fallback, blocks=blocks)


def _validated_response(response: httpx.Response) -> dict[str, str]:
    try:
        raw = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
        raise _safe_error(
            code="slack.invalid_response",
            category="transient",
            retryable=True,
            message="Slack returned an invalid response.",
        ) from error
    if not isinstance(raw, dict) or not isinstance(raw.get("ok"), bool):
        raise _safe_error(
            code="slack.invalid_response",
            category="transient",
            retryable=True,
            message="Slack returned an invalid response.",
        )
    if raw["ok"] is not True:
        provider_code = raw.get("error")
        category: DeliveryErrorCategory = "permanent"
        if provider_code == "ratelimited":
            code, category, retryable = "slack.rate_limited", "transient", True
        elif provider_code in _AUTH_CONFIGURATION_ERRORS:
            code, category, retryable = (
                "slack.authentication_rejected",
                "configuration",
                False,
            )
        elif provider_code in _DESTINATION_CONFIGURATION_ERRORS:
            code, category, retryable = (
                "slack.destination_rejected",
                "configuration",
                False,
            )
        elif provider_code in _TRANSIENT_PROVIDER_ERRORS:
            code, category, retryable = "slack.provider_failure", "transient", True
        else:
            code, category, retryable = "slack.provider_rejected", "permanent", False
        raise _safe_error(
            code=code,
            category=category,
            retryable=retryable,
            message="Slack did not accept the configured delivery request.",
        )
    timestamp = raw.get("ts")
    if (
        not isinstance(timestamp, str)
        or len(timestamp) > 64
        or not re.fullmatch(r"[0-9]{1,24}\.[0-9]{1,24}", timestamp)
    ):
        raise _safe_error(
            code="slack.invalid_response",
            category="transient",
            retryable=True,
            message="Slack returned an invalid response.",
        )
    return {"ts": timestamp}


def _raise_for_http_status(status_code: int) -> None:
    if status_code == 429:
        raise _safe_error(
            code="slack.rate_limited",
            category="transient",
            retryable=True,
            message="Slack rate-limited the delivery request.",
        )
    if status_code >= 500:
        raise _safe_error(
            code="slack.unavailable",
            category="transient",
            retryable=True,
            message="Slack is temporarily unavailable.",
        )
    if status_code >= 400:
        category: DeliveryErrorCategory = (
            "configuration" if status_code in {401, 403} else "permanent"
        )
        raise _safe_error(
            code="slack.request_rejected",
            category=category,
            retryable=False,
            message="Slack rejected the configured delivery request.",
        )
    if status_code >= 300:
        raise _safe_error(
            code="slack.redirect_rejected",
            category="configuration",
            retryable=False,
            message="The configured Slack endpoint returned a redirect.",
        )


def _validate_response_size_header(response: httpx.Response) -> None:
    content_length = response.headers.get("content-length")
    if content_length is None:
        return
    if not content_length.isascii() or not content_length.isdecimal():
        raise _invalid_response_error()
    if int(content_length) > _RESPONSE_BODY_LIMIT:
        raise _invalid_response_error()


def _invalid_response_error() -> DeliveryConnectorError:
    return _safe_error(
        code="slack.invalid_response",
        category="transient",
        retryable=True,
        message="Slack returned an invalid response.",
    )


def _report_summary(report: RunReport) -> str:
    if str(report.format) == "json" and len(report.content) <= _MESSAGE_SCAN_LIMIT:
        summary = _json_report_summary(report.content)
        if summary is not None:
            return summary
    source = report.content[:_MESSAGE_SCAN_LIMIT]
    if str(report.format) == "html":
        parser = _TextExtractor()
        parser.feed(source)
        source = " ".join(parser.parts)
    source = re.sub(r"[#*_`~]+", " ", source)
    source = re.sub(r"\s+", " ", source).strip()
    return source or "The immutable Qualestra report is ready."


def _json_report_summary(content: str) -> str | None:
    try:
        raw = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    technical = raw.get("technical_summary")
    executive = raw.get("executive_summary")
    release = raw.get("release_summary")
    if not all(isinstance(item, dict) for item in (technical, executive, release)):
        return None
    assert isinstance(technical, dict)
    assert isinstance(executive, dict)
    assert isinstance(release, dict)
    outcome = release.get("outcome")
    finding_count = technical.get("verified_defect_count")
    headline = executive.get("headline")
    narrative = executive.get("narrative")
    parts: list[str] = []
    if isinstance(outcome, str) and isinstance(finding_count, int):
        parts.append(f"{outcome} · {finding_count} verified findings")
    for candidate in (headline, narrative):
        if isinstance(candidate, str) and candidate.strip():
            parts.append(candidate.strip())
    return "\n".join(parts) if parts else None


def _slack_escape(value: str) -> str:
    return escape(value, quote=False)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _safe_error(
    *,
    code: str,
    category: DeliveryErrorCategory,
    retryable: bool,
    message: str,
) -> DeliveryConnectorError:
    return DeliveryConnectorError(
        code=code,
        category=category,
        retryable=retryable,
        message=message,
    )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())
