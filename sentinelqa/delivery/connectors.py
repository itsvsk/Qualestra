from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from sentinelqa.domain.models import DeliveryError, RunReport


@dataclass(frozen=True, slots=True)
class DeliveryReceipt:
    external_id: str | None = None
    external_url: str | None = None


class DeliveryConnectorError(Exception):
    """A provider failure containing only validated, persistence-safe metadata."""

    def __init__(
        self,
        *,
        code: str,
        category: Literal["transient", "permanent", "policy", "configuration", "unknown"],
        retryable: bool,
        message: str,
    ) -> None:
        self.metadata = DeliveryError(
            code=code,
            category=category,
            retryable=retryable,
            redacted_message=message,
        )
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DeliveryDestination:
    """Non-secret destination metadata safe to expose through the control API."""

    connector_id: str
    destination_id: str
    display_name: str
    kind: Literal["artifact", "ticket", "alert"]
    provider: str
    supported_modes: tuple[str, ...] = ("dry_run", "manual", "automatic")

    def __post_init__(self) -> None:
        if not self.connector_id or not self.destination_id:
            raise ValueError("delivery destination identifiers must not be empty")
        if not self.display_name.strip() or len(self.display_name) > 120:
            raise ValueError("delivery destination display name is invalid")
        if not self.provider.strip() or len(self.provider) > 40:
            raise ValueError("delivery destination provider is invalid")


class DeliveryConnector(Protocol):
    connector_id: str

    async def deliver(
        self,
        report: RunReport,
        *,
        destination_id: str,
        idempotency_key: str,
    ) -> DeliveryReceipt: ...


class AsyncDeliveryResource(Protocol):
    async def aclose(self) -> None: ...


class DeliveryRegistry:
    """Static connector/destination registry controlled by server configuration."""

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], DeliveryConnector] = {}
        self._catalog: dict[tuple[str, str], DeliveryDestination] = {}
        self._resources: dict[int, AsyncDeliveryResource] = {}

    def register(
        self,
        connector: DeliveryConnector,
        *destination_ids: str,
        display_name: str | None = None,
        kind: Literal["artifact", "ticket", "alert"] = "artifact",
        provider: str | None = None,
    ) -> None:
        if not destination_ids:
            raise ValueError("a delivery connector requires at least one configured destination")
        for destination_id in destination_ids:
            key = (connector.connector_id, destination_id)
            if key in self._entries:
                raise ValueError(f"delivery destination is already registered: {key!r}")
            self._entries[key] = connector
            self._catalog[key] = DeliveryDestination(
                connector_id=connector.connector_id,
                destination_id=destination_id,
                display_name=display_name or destination_id,
                kind=kind,
                provider=provider or connector.connector_id,
            )

    def resolve(self, connector_id: str, destination_id: str) -> DeliveryConnector:
        try:
            return self._entries[(connector_id, destination_id)]
        except KeyError as error:
            raise ValueError("delivery connector or destination is not configured") from error

    def destinations(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self._entries))

    def catalog(self) -> tuple[DeliveryDestination, ...]:
        return tuple(self._catalog[key] for key in sorted(self._catalog))

    def manage(self, resource: AsyncDeliveryResource) -> None:
        self._resources[id(resource)] = resource

    async def aclose(self) -> None:
        for resource in reversed(tuple(self._resources.values())):
            await resource.aclose()
        self._resources.clear()


class NoopConnector:
    connector_id = "noop"

    async def deliver(
        self,
        report: RunReport,
        *,
        destination_id: str,
        idempotency_key: str,
    ) -> DeliveryReceipt:
        del report, destination_id
        return DeliveryReceipt(external_id=idempotency_key)


class FilesystemConnector:
    connector_id = "filesystem"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    async def deliver(
        self,
        report: RunReport,
        *,
        destination_id: str,
        idempotency_key: str,
    ) -> DeliveryReceipt:
        del destination_id
        extension = "md" if str(report.format) == "markdown" else str(report.format)
        filename = f"{idempotency_key.removeprefix('sha256:')}.{extension}"
        target = (self.root / filename).resolve()
        if self.root not in target.parents:
            raise ValueError("delivery target escaped the configured filesystem root")

        def write_once() -> None:
            self.root.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.read_text(encoding="utf-8") != report.content:
                    raise ValueError("idempotent delivery target contains different content")
                return
            target.write_text(report.content, encoding="utf-8")

        await asyncio.to_thread(write_once)
        return DeliveryReceipt(external_id=filename)


def build_delivery_registry_from_env() -> DeliveryRegistry:
    registry = DeliveryRegistry()
    registry.register(
        NoopConnector(),
        "local-noop",
        display_name="Local no-op",
        kind="artifact",
        provider="offline",
    )
    artifact_root = Path(os.getenv("ARTIFACT_ROOT", "artifacts"))
    delivery_root = os.getenv("SENTINEL_DELIVERY_DIR", str(artifact_root / "deliveries"))
    registry.register(
        FilesystemConnector(delivery_root),
        "local-report",
        display_name="Local report archive",
        kind="artifact",
        provider="filesystem",
    )
    provider_config = os.getenv("SENTINEL_DELIVERY_CONFIG")
    if provider_config:
        from .configuration import (
            load_delivery_provider_configuration,
            register_provider_destinations,
        )

        register_provider_destinations(
            registry,
            load_delivery_provider_configuration(provider_config),
        )
    return registry
