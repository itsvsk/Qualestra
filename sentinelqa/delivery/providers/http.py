from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import httpx

_DEFAULT_MAX_RESPONSE_BYTES = 1_000_000


@dataclass(frozen=True, slots=True)
class ProviderHttpResponse:
    """Bounded response surface consumed by provider connectors."""

    status_code: int
    payload: object | None


class AsyncHttpTransport(Protocol):
    """Injectable HTTP boundary; tests do not require network access."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, object] | None = None,
    ) -> ProviderHttpResponse: ...


class HttpxAsyncTransport:
    """Production HTTP transport with redirects disabled by the supplied client."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if client.follow_redirects:
            raise ValueError("provider HTTP clients must not follow redirects")
        if max_response_bytes < 1 or max_response_bytes > _DEFAULT_MAX_RESPONSE_BYTES:
            raise ValueError("provider response limit must be between 1 and 1000000 bytes")
        self._client = client
        self._max_response_bytes = max_response_bytes

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, object] | None = None,
    ) -> ProviderHttpResponse:
        request = self._client.build_request(
            method,
            url,
            headers=headers,
            json=json_body,
        )
        response = await self._client.send(request, stream=True)
        payload: object | None = None
        content = bytearray()
        try:
            declared_length = response.headers.get("content-length")
            if declared_length is not None:
                try:
                    if int(declared_length) > self._max_response_bytes:
                        raise ProviderResponseTooLarge
                except ValueError:
                    raise ProviderTransportFailure from None
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > self._max_response_bytes:
                    raise ProviderResponseTooLarge
        finally:
            await response.aclose()
        if content:
            try:
                payload = json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = None
        return ProviderHttpResponse(status_code=response.status_code, payload=payload)

    async def aclose(self) -> None:
        """Close the owned client during the application lifespan shutdown."""

        await self._client.aclose()


class ProviderTransportFailure(RuntimeError):
    """Safe, body-free transport failure."""


class ProviderResponseTooLarge(ProviderTransportFailure):
    """The provider response exceeded the configured byte budget."""
