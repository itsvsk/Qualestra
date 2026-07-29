"""Concrete policy-enforcing HTTP transport for portable adapters."""

from __future__ import annotations

import json
from time import perf_counter
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import JsonValue, TypeAdapter

from sentinelqa.adapters.auth import AuthMaterial, FormCredentials
from sentinelqa.adapters.invariants import HttpMethod, SafeHttpResponse
from sentinelqa.domain.enums import SafetyClass
from sentinelqa.domain.models import CandidateAction
from sentinelqa.policy.engine import PolicyEngine
from sentinelqa.policy.redaction import redact_headers, redact_value
from sentinelqa.storage.artifacts import ArtifactStore


class PolicySafeHttpTool:
    """Execute trusted adapter requests after deterministic policy authorization."""

    def __init__(
        self,
        *,
        base_url: str,
        policy: PolicyEngine,
        artifacts: ArtifactStore,
        client: httpx.AsyncClient | None = None,
        timeout: float = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._origin = _origin(base_url)
        if self._origin not in policy.allowed_origins:
            raise ValueError("HTTP tool base URL is not policy-allowlisted")
        self._policy = policy
        self._artifacts = artifacts
        self._client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None

    async def request(
        self,
        method: HttpMethod,
        path: str,
        *,
        json_body: JsonValue | None,
        authentication: AuthMaterial,
    ) -> SafeHttpResponse:
        response = await self._request(
            method, path, json_body=json_body, authentication=authentication
        )
        body: JsonValue | None
        try:
            body = TypeAdapter(JsonValue).validate_python(response.json())
        except (ValueError, TypeError):
            body = None
        return SafeHttpResponse(
            status=response.status_code,
            duration_ms=int(response.extensions["sentinelqa_duration_ms"]),
            json_body=body,
            evidence_ref=str(response.extensions["sentinelqa_evidence_ref"]),
        )

    async def form_login(self, credentials: FormCredentials) -> AuthMaterial:
        response = await self._request(
            "POST",
            credentials.login_path,
            form_body=credentials.fields,
            authentication=AuthMaterial(),
        )
        if response.status_code not in credentials.expected_statuses:
            raise PermissionError(f"form login returned unexpected status {response.status_code}")
        return AuthMaterial(cookies=dict(response.cookies))

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: JsonValue | None = None,
        form_body: dict[str, str] | None = None,
        authentication: AuthMaterial,
    ) -> httpx.Response:
        if not path.startswith("/") or path.startswith("//") or "://" in path:
            raise ValueError("HTTP tool accepts only target-relative paths")
        if any(part == ".." for part in path.split("/")):
            raise ValueError("path traversal is not allowed")
        url = urljoin(self._base_url, path.lstrip("/"))
        if _origin(url) != self._origin:
            raise PermissionError("HTTP request escaped the configured origin")
        safety = SafetyClass.SAFE if method == "GET" else SafetyClass.CONTROLLED
        self._policy.require_authorized(
            CandidateAction(
                action_type="api_request",
                input=f"{method} {path}",
                safety_class=safety,
                target_origin=self._origin,
            )
        )
        started = perf_counter()
        response = await self._client.request(
            method,
            url,
            headers=authentication.headers,
            cookies=authentication.cookies or None,
            json=json_body,
            data=form_body,
        )
        duration_ms = int((perf_counter() - started) * 1000)
        evidence = {
            "request": {
                "method": method,
                "url": url,
                "headers": redact_headers(authentication.headers),
                "body": redact_value(json_body if form_body is None else form_body),
            },
            "response": {
                "status": response.status_code,
                "headers": redact_headers(dict(response.headers)),
                "body": redact_value(response.text[:20_000]),
            },
            "duration_ms": duration_ms,
        }
        artifact = self._artifacts.put(
            kind="request_response",
            content=json.dumps(evidence, sort_keys=True).encode(),
            mime_type="application/json",
            redact=True,
        )
        response.extensions["sentinelqa_duration_ms"] = duration_ms
        response.extensions["sentinelqa_evidence_ref"] = artifact.uri
        return response


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("base URL must be absolute HTTP(S)")
    return f"{parsed.scheme}://{parsed.netloc}"
