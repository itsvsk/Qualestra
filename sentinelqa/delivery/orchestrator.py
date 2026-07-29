from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sentinelqa.blackboard.port import Blackboard
from sentinelqa.domain.enums import DeliveryMode, DeliveryStatus
from sentinelqa.domain.events import EventEnvelope
from sentinelqa.domain.models import (
    DeliveryError,
    DeliveryRecord,
    DeliveryRequest,
    DeliveryResult,
    RunReport,
)

from .connectors import DeliveryConnectorError, DeliveryRegistry


class DeliveryOrchestrator:
    """Deterministic state-machine owner for connector side effects."""

    def __init__(self, blackboard: Blackboard, registry: DeliveryRegistry) -> None:
        self.blackboard = blackboard
        self.registry = registry

    async def create(
        self,
        *,
        run_id: UUID,
        report: RunReport,
        connector_id: str,
        destination_id: str,
        mode: DeliveryMode,
    ) -> DeliveryRecord:
        if report.run_id != run_id:
            raise ValueError("delivery report belongs to another run")
        self.registry.resolve(connector_id, destination_id)
        request = DeliveryRequest(
            run_id=run_id,
            report_id=report.report_id,
            report_digest=report.digest,
            report_format=report.format,
            connector_id=connector_id,
            destination_id=destination_id,
            mode=mode,
            idempotency_key=DeliveryRequest.stable_key(
                run_id,
                report.digest,
                connector_id,
                destination_id,
                mode,
            ),
        )
        record = await self.blackboard.create_delivery(request)
        if record.request.delivery_id == request.delivery_id:
            await self._event(
                record,
                "delivery.created",
                {"status": str(record.status), "mode": str(record.request.mode)},
            )
        if record.status == DeliveryStatus.QUEUED:
            return await self.process(record.request.delivery_id)
        return record

    async def approve(self, delivery_id: UUID) -> DeliveryRecord:
        current = await self._required(delivery_id)
        if current.status != DeliveryStatus.PENDING_APPROVAL:
            raise ValueError("only pending manual deliveries can be approved")
        queued = await self.blackboard.update_delivery(
            delivery_id,
            DeliveryStatus.QUEUED,
            approved_at=datetime.now(UTC),
        )
        await self._event(queued, "delivery.approved", {"status": str(queued.status)})
        return await self.process(delivery_id)

    async def retry(self, delivery_id: UUID) -> DeliveryRecord:
        current = await self._required(delivery_id)
        if current.status != DeliveryStatus.FAILED:
            raise ValueError("only failed deliveries can be retried")
        queued = await self.blackboard.update_delivery(delivery_id, DeliveryStatus.QUEUED)
        await self._event(
            queued,
            "delivery.retried",
            {"previous_attempts": queued.attempt_count},
        )
        return await self.process(delivery_id)

    async def process(self, delivery_id: UUID) -> DeliveryRecord:
        claimed = await self.blackboard.claim_delivery(delivery_id)
        if claimed is None:
            return await self._required(delivery_id)
        await self._event(
            claimed,
            "delivery.processing",
            {"attempt": claimed.attempt_count + 1},
        )
        report = await self.blackboard.run_report(claimed.request.report_id)
        if report is None or report.digest != claimed.request.report_digest:
            return await self._record_failure(
                claimed,
                code="report_unavailable",
                category="permanent",
                retryable=False,
                message="Persisted report content is unavailable or does not match its digest.",
            )
        try:
            connector = self.registry.resolve(
                claimed.request.connector_id, claimed.request.destination_id
            )
            receipt = await connector.deliver(
                report,
                destination_id=claimed.request.destination_id,
                idempotency_key=claimed.request.idempotency_key,
            )
        except DeliveryConnectorError as error:
            return await self._record_failure(
                claimed,
                code=error.metadata.code,
                category=error.metadata.category,
                retryable=error.metadata.retryable,
                message=error.metadata.redacted_message,
            )
        except ValueError:
            return await self._record_failure(
                claimed,
                code="delivery_configuration",
                category="configuration",
                retryable=False,
                message="The configured delivery destination rejected the report.",
            )
        except Exception:
            return await self._record_failure(
                claimed,
                code="connector_failure",
                category="unknown",
                retryable=True,
                message="The delivery connector failed; sensitive details were not retained.",
            )
        result = DeliveryResult(
            delivery_id=delivery_id,
            attempt=claimed.attempt_count + 1,
            status="succeeded",
            connector_id=claimed.request.connector_id,
            external_id=receipt.external_id,
            external_url=receipt.external_url,
        )
        completed = await self.blackboard.record_delivery_result(result)
        await self._event(
            completed,
            "delivery.succeeded",
            {"attempt": result.attempt, "external_id": result.external_id},
        )
        return completed

    async def recover(self) -> None:
        state = await self.blackboard.load_state()
        for record in sorted(
            state.deliveries, key=lambda item: (item.request.created_at, item.request.delivery_id)
        ):
            if record.status == DeliveryStatus.QUEUED:
                await self.process(record.request.delivery_id)
            elif record.status == DeliveryStatus.PROCESSING:
                await self._record_failure(
                    record,
                    code="delivery_interrupted",
                    category="transient",
                    retryable=True,
                    message="Delivery processing was interrupted before completion.",
                )

    async def aclose(self) -> None:
        await self.registry.aclose()

    async def _record_failure(
        self,
        record: DeliveryRecord,
        *,
        code: str,
        category: Literal["transient", "permanent", "policy", "configuration", "unknown"],
        retryable: bool,
        message: str,
    ) -> DeliveryRecord:
        error = DeliveryError(
            code=code,
            category=category,
            retryable=retryable,
            redacted_message=message,
        )
        result = DeliveryResult(
            delivery_id=record.request.delivery_id,
            attempt=record.attempt_count + 1,
            status="failed",
            connector_id=record.request.connector_id,
            error=error,
        )
        failed = await self.blackboard.record_delivery_result(result)
        await self._event(
            failed,
            "delivery.failed",
            {"attempt": result.attempt, "error_code": error.code, "retryable": error.retryable},
        )
        return failed

    async def _required(self, delivery_id: UUID) -> DeliveryRecord:
        record = await self.blackboard.delivery(delivery_id)
        if record is None:
            raise KeyError(delivery_id)
        return record

    async def _event(
        self, record: DeliveryRecord, event_type: str, payload: dict[str, object]
    ) -> None:
        await self.blackboard.append_event(
            EventEnvelope(
                event_type=event_type,
                run_id=record.request.run_id,
                correlation_id=record.request.delivery_id,
                producer="control-api",
                payload=payload,
            )
        )
