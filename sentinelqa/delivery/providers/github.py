from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import NoReturn
from urllib.parse import quote, urlencode, urlsplit

from sentinelqa.delivery.connectors import DeliveryConnectorError, DeliveryReceipt
from sentinelqa.domain.models import RunReport

from .http import AsyncHttpTransport, ProviderHttpResponse
from .ticketing import build_ticket_payload

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_ENV_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


@dataclass(frozen=True, slots=True)
class GitHubIssuesDestination:
    destination_id: str
    repository: str
    token_env: str
    api_base_url: str = "https://api.github.com"
    web_base_url: str = "https://github.com"
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _REPOSITORY_PATTERN.fullmatch(self.repository):
            raise ValueError("GitHub repository must be a static owner/name identifier")
        if not _ENV_PATTERN.fullmatch(self.token_env):
            raise ValueError("GitHub token must be referenced by a static environment name")
        object.__setattr__(self, "api_base_url", _validated_base_url(self.api_base_url))
        object.__setattr__(self, "web_base_url", _validated_base_url(self.web_base_url))


class GitHubIssuesConnector:
    connector_id = "github_issues"

    def __init__(
        self,
        destinations: Mapping[str, GitHubIssuesDestination],
        transport: AsyncHttpTransport,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._destinations = dict(destinations)
        if any(
            key != destination.destination_id for key, destination in self._destinations.items()
        ):
            raise ValueError("GitHub destination keys must match their static identifiers")
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
        token = self._environ.get(destination.token_env)
        if not token:
            raise DeliveryConnectorError(
                code="github.credentials_missing",
                category="configuration",
                retryable=False,
                message="The configured GitHub credential reference is unavailable.",
            )
        ticket = build_ticket_payload(
            report,
            idempotency_key,
            base_labels=destination.labels,
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        repository_path = quote(destination.repository, safe="/")
        query = urlencode(
            {
                "q": (
                    f"repo:{destination.repository} "
                    f'"{idempotency_key.removeprefix("sha256:")}" in:body is:issue'
                ),
                "per_page": "10",
            }
        )
        search = await self._request(
            "GET",
            f"{destination.api_base_url}/search/issues?{query}",
            headers=headers,
        )
        _expect_status(search, {200}, "github")
        existing = _existing_issue(search.payload, ticket.idempotency_marker)
        if existing is not None:
            return _github_receipt(destination, existing)

        created = await self._request(
            "POST",
            f"{destination.api_base_url}/repos/{repository_path}/issues",
            headers=headers,
            json_body={
                "title": ticket.title,
                "body": ticket.body,
                "labels": list(ticket.labels),
            },
        )
        _expect_status(created, {201}, "github")
        issue = _issue_identity(created.payload)
        return _github_receipt(destination, issue)

    def _destination(self, destination_id: str) -> GitHubIssuesDestination:
        try:
            return self._destinations[destination_id]
        except KeyError as error:
            raise DeliveryConnectorError(
                code="github.destination_unconfigured",
                category="configuration",
                retryable=False,
                message="The GitHub issue destination is not statically configured.",
            ) from error

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
                code="github.transport_failure",
                category="transient",
                retryable=True,
                message="GitHub could not be reached; provider details were not retained.",
            ) from None


def _expect_status(response: ProviderHttpResponse, expected: set[int], provider: str) -> None:
    if response.status_code in expected:
        return
    if response.status_code in {401, 403}:
        raise DeliveryConnectorError(
            code=f"{provider}.authentication_rejected",
            category="configuration",
            retryable=False,
            message="The provider rejected the configured authentication.",
        )
    if response.status_code in {408, 425, 429} or response.status_code >= 500:
        raise DeliveryConnectorError(
            code=f"{provider}.temporarily_unavailable",
            category="transient",
            retryable=True,
            message="The provider is temporarily unavailable; response details were not retained.",
        )
    raise DeliveryConnectorError(
        code=f"{provider}.request_rejected",
        category="permanent",
        retryable=False,
        message="The provider rejected the bounded ticket request.",
    )


def _existing_issue(payload: object | None, marker: str) -> tuple[int, str] | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        _invalid_response("github")
    assert isinstance(payload, dict)
    items = payload["items"]
    assert isinstance(items, list)
    for candidate in items:
        if isinstance(candidate, dict) and marker in str(candidate.get("body", "")):
            return _issue_identity(candidate)
    return None


def _issue_identity(payload: object | None) -> tuple[int, str]:
    if not isinstance(payload, dict):
        _invalid_response("github")
    assert isinstance(payload, dict)
    number = payload.get("number")
    node_id = payload.get("node_id")
    if (
        not isinstance(number, int)
        or isinstance(number, bool)
        or number < 1
        or not isinstance(node_id, str)
        or not node_id
        or len(node_id) > 240
    ):
        _invalid_response("github")
    return number, node_id


def _github_receipt(
    destination: GitHubIssuesDestination,
    identity: tuple[int, str],
) -> DeliveryReceipt:
    number, node_id = identity
    return DeliveryReceipt(
        external_id=node_id,
        external_url=f"{destination.web_base_url}/{destination.repository}/issues/{number}",
    )


def _invalid_response(provider: str) -> NoReturn:
    raise DeliveryConnectorError(
        code=f"{provider}.response_invalid",
        category="unknown",
        retryable=True,
        message="The provider returned an invalid response; its content was not retained.",
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
