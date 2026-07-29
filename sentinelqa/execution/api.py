from __future__ import annotations

from time import perf_counter
from urllib.parse import urljoin, urlsplit

import httpx

from sentinelqa.policy.redaction import redact_headers, redact_value
from sentinelqa.storage.artifacts import ArtifactStore


class AllowlistedAPIClient:
    def __init__(
        self,
        base_url: str,
        allowed_origins: set[str],
        artifacts: ArtifactStore,
        timeout: float = 10,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.allowed_origins = {origin.rstrip("/") for origin in allowed_origins}
        self.artifacts = artifacts
        self.client = httpx.AsyncClient(timeout=timeout, follow_redirects=False)

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: object | None = None,
    ) -> dict[str, object]:
        if not path.startswith("/") or "//" in path:
            raise ValueError("API path must be an absolute target-relative path")
        url = urljoin(self.base_url, path.lstrip("/"))
        parsed = urlsplit(url)
        if f"{parsed.scheme}://{parsed.netloc}" not in self.allowed_origins:
            raise PermissionError("API origin is not allowlisted")
        started = perf_counter()
        response = await self.client.request(method, url, headers=headers, json=json_body)
        elapsed = int((perf_counter() - started) * 1000)
        evidence = {
            "request": {
                "method": method.upper(),
                "url": url,
                "headers": redact_headers(headers or {}),
                "body": redact_value(json_body),
            },
            "response": {
                "status": response.status_code,
                "headers": redact_headers(dict(response.headers)),
                "body": redact_value(response.text[:20_000]),
            },
            "duration_ms": elapsed,
        }
        import json

        artifact = self.artifacts.put(
            kind="request_response",
            content=json.dumps(evidence, sort_keys=True).encode(),
            mime_type="application/json",
            redact=True,
        )
        return {
            "status": response.status_code,
            "duration_ms": elapsed,
            "evidence_ref": artifact.uri,
        }

    async def close(self) -> None:
        await self.client.aclose()
