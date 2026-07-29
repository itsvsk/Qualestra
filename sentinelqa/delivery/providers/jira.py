from __future__ import annotations

import base64
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NoReturn
from urllib.parse import quote, urlsplit

from sentinelqa.delivery.connectors import DeliveryConnectorError, DeliveryReceipt
from sentinelqa.domain.models import RunReport

from .http import AsyncHttpTransport, ProviderHttpResponse
from .ticketing import build_ticket_payload, idempotency_label

_PROJECT_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,19}$")
_ISSUE_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,19}-[1-9][0-9]*$")
_ENV_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@dataclass(frozen=True, slots=True)
class JiraDestination:
    destination_id: str
    base_url: str
    project_key: str
    api_token_env: str
    user_email_env: str | None = None
    issue_type: str = "Bug"
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _PROJECT_PATTERN.fullmatch(self.project_key):
            raise ValueError("Jira project must be a static project key")
        if not _ENV_PATTERN.fullmatch(self.api_token_env):
            raise ValueError("Jira token must be referenced by a static environment name")
        if self.user_email_env is not None and not _ENV_PATTERN.fullmatch(self.user_email_env):
            raise ValueError("Jira user must be referenced by a static environment name")
        if not self.issue_type.strip() or len(self.issue_type) > 80:
            raise ValueError("Jira issue type must be a bounded static value")
        object.__setattr__(self, "base_url", _validated_base_url(self.base_url))


class JiraConnector:
    connector_id = "jira"

    def __init__(
        self,
        destinations: Mapping[str, JiraDestination],
        transport: AsyncHttpTransport,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._destinations = dict(destinations)
        if any(
            key != destination.destination_id for key, destination in self._destinations.items()
        ):
            raise ValueError("Jira destination keys must match their static identifiers")
        self._transport = transport
        self._environ = environ if environ is not None else os.environ

    async def deliver(
        self,
        report: RunReport,
        *,
        destination_id: str,
        idempotency_key: str,
    ) -> DeliveryReceipt:
        destination = self._destination(destination_id)
        headers = self._headers(destination)
        ticket = build_ticket_payload(
            report,
            idempotency_key,
            base_labels=destination.labels,
        )
        dedupe_label = idempotency_label(idempotency_key)
        search = await self._request(
            "POST",
            f"{destination.base_url}/rest/api/3/search/jql",
            headers=headers,
            json_body={
                "jql": (
                    f'project = "{destination.project_key}" '
                    f'AND labels = "{dedupe_label}" ORDER BY created DESC'
                ),
                "fields": ["key"],
                "maxResults": 2,
            },
        )
        _expect_status(search, {200})
        existing = _existing_issue(search.payload, destination.project_key)
        if existing is not None:
            return _jira_receipt(destination, existing)

        created = await self._request(
            "POST",
            f"{destination.base_url}/rest/api/3/issue",
            headers=headers,
            json_body={
                "fields": {
                    "project": {"key": destination.project_key},
                    "summary": ticket.title,
                    "issuetype": {"name": destination.issue_type},
                    "labels": [*ticket.labels, dedupe_label],
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": ticket.body}],
                            }
                        ],
                    },
                }
            },
        )
        _expect_status(created, {201})
        return _jira_receipt(destination, _issue_key(created.payload, destination.project_key))

    def _destination(self, destination_id: str) -> JiraDestination:
        try:
            return self._destinations[destination_id]
        except KeyError as error:
            raise DeliveryConnectorError(
                code="jira.destination_unconfigured",
                category="configuration",
                retryable=False,
                message="The Jira destination is not statically configured.",
            ) from error

    def _headers(self, destination: JiraDestination) -> dict[str, str]:
        token = self._environ.get(destination.api_token_env)
        if not token:
            _credentials_missing()
        assert token is not None
        if destination.user_email_env is None:
            authorization = f"Bearer {token}"
        else:
            email = self._environ.get(destination.user_email_env)
            if not email:
                _credentials_missing()
            assert email is not None
            encoded = base64.b64encode(f"{email}:{token}".encode()).decode("ascii")
            authorization = f"Basic {encoded}"
        return {
            "Accept": "application/json",
            "Authorization": authorization,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, object] | None = None,
    ) -> ProviderHttpResponse:
        try:
            return await self._transport.request(
                method,
                url,
                headers=headers,
                json_body=json_body,
            )
        except DeliveryConnectorError:
            raise
        except Exception:
            raise DeliveryConnectorError(
                code="jira.transport_failure",
                category="transient",
                retryable=True,
                message="Jira could not be reached; provider details were not retained.",
            ) from None


def _expect_status(response: ProviderHttpResponse, expected: set[int]) -> None:
    if response.status_code in expected:
        return
    if response.status_code in {401, 403}:
        raise DeliveryConnectorError(
            code="jira.authentication_rejected",
            category="configuration",
            retryable=False,
            message="Jira rejected the configured authentication.",
        )
    if response.status_code in {408, 425, 429} or response.status_code >= 500:
        raise DeliveryConnectorError(
            code="jira.temporarily_unavailable",
            category="transient",
            retryable=True,
            message="Jira is temporarily unavailable; response details were not retained.",
        )
    raise DeliveryConnectorError(
        code="jira.request_rejected",
        category="permanent",
        retryable=False,
        message="Jira rejected the bounded ticket request.",
    )


def _existing_issue(payload: object | None, project_key: str) -> str | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("issues"), list):
        _invalid_response()
    assert isinstance(payload, dict)
    issues = payload["issues"]
    assert isinstance(issues, list)
    if not issues:
        return None
    if len(issues) > 1:
        raise DeliveryConnectorError(
            code="jira.idempotency_conflict",
            category="permanent",
            retryable=False,
            message="Multiple Jira issues matched the stable delivery marker.",
        )
    return _issue_key(issues[0], project_key)


def _issue_key(payload: object | None, project_key: str) -> str:
    if not isinstance(payload, dict):
        _invalid_response()
    assert isinstance(payload, dict)
    key = payload.get("key")
    if not isinstance(key, str) or not _ISSUE_KEY_PATTERN.fullmatch(key):
        _invalid_response()
    if not key.startswith(f"{project_key}-"):
        _invalid_response()
    return key


def _jira_receipt(destination: JiraDestination, key: str) -> DeliveryReceipt:
    return DeliveryReceipt(
        external_id=key,
        external_url=f"{destination.base_url}/browse/{quote(key, safe='-')}",
    )


def _credentials_missing() -> NoReturn:
    raise DeliveryConnectorError(
        code="jira.credentials_missing",
        category="configuration",
        retryable=False,
        message="A configured Jira credential reference is unavailable.",
    )


def _invalid_response() -> NoReturn:
    raise DeliveryConnectorError(
        code="jira.response_invalid",
        category="unknown",
        retryable=True,
        message="Jira returned an invalid response; its content was not retained.",
    )


def _validated_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("provider base URLs must be static credential-free HTTPS URLs")
    return value.rstrip("/")
