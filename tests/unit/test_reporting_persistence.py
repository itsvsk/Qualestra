from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sentinelqa.blackboard.memory import InMemoryBlackboard
from sentinelqa.domain.enums import (
    DeliveryMode,
    DeliveryStatus,
    ReportFormat,
    ReportKind,
)
from sentinelqa.domain.models import (
    DeliveryError,
    DeliveryRequest,
    DeliveryResult,
    RunReport,
    stable_digest,
)


def make_report(run_id, *, content: str = '{"verified_defects":1}') -> RunReport:
    return RunReport(
        run_id=run_id,
        kind=ReportKind.TECHNICAL,
        format=ReportFormat.JSON,
        content=content,
        digest=stable_digest(content),
        source_fingerprint=stable_digest(run_id, "evidence-v1"),
        generated_at=datetime(2026, 7, 27, 10, tzinfo=UTC),
    )


def make_request(report: RunReport, **updates) -> DeliveryRequest:
    values = {
        "run_id": report.run_id,
        "report_id": report.report_id,
        "report_digest": report.digest,
        "report_format": report.format,
        "connector_id": "filesystem",
        "destination_id": "local-audit",
        "mode": DeliveryMode.MANUAL,
        "idempotency_key": DeliveryRequest.stable_key(
            report.run_id,
            report.digest,
            "filesystem",
            "local-audit",
            DeliveryMode.MANUAL,
        ),
        "created_at": report.generated_at,
    }
    values.update(updates)
    return DeliveryRequest(**values)


@pytest.mark.asyncio
async def test_report_snapshot_and_delivery_creation_are_idempotent(run_id):
    board = InMemoryBlackboard()
    report = make_report(run_id)
    assert await board.put_run_report(report) == report

    regenerated = report.model_copy(update={"report_id": uuid4()})
    assert await board.put_run_report(regenerated) == report
    assert await board.reports(run_id) == [report]

    request = make_request(report)
    created = await board.create_delivery(request)
    assert created.status == DeliveryStatus.PENDING_APPROVAL
    duplicate = request.model_copy(update={"delivery_id": uuid4()})
    assert await board.create_delivery(duplicate) == created
    assert await board.deliveries(run_id) == [created]


@pytest.mark.asyncio
async def test_delivery_attempts_are_claimed_and_recorded_atomically(run_id):
    board = InMemoryBlackboard()
    report = await board.put_run_report(make_report(run_id))
    request = make_request(report)
    await board.create_delivery(request)
    approved_at = report.generated_at + timedelta(minutes=1)
    queued = await board.update_delivery(
        request.delivery_id,
        DeliveryStatus.QUEUED,
        approved_at=approved_at,
    )
    assert queued.approved_at == approved_at

    claimed = await board.claim_delivery(request.delivery_id)
    assert claimed is not None
    assert claimed.status == DeliveryStatus.PROCESSING
    assert await board.claim_delivery(request.delivery_id) is None

    failure = DeliveryResult(
        delivery_id=request.delivery_id,
        attempt=1,
        status="failed",
        connector_id=request.connector_id,
        error=DeliveryError(
            code="sink.unavailable",
            category="transient",
            retryable=True,
            redacted_message="Configured sink is unavailable",
        ),
        recorded_at=report.generated_at + timedelta(minutes=2),
    )
    failed = await board.record_delivery_result(failure)
    assert failed.status == DeliveryStatus.FAILED
    assert failed.attempt_count == 1

    await board.update_delivery(
        request.delivery_id,
        DeliveryStatus.QUEUED,
        next_attempt_at=report.generated_at + timedelta(minutes=3),
    )
    assert await board.claim_delivery(request.delivery_id) is not None
    success = DeliveryResult(
        delivery_id=request.delivery_id,
        attempt=2,
        status="succeeded",
        connector_id=request.connector_id,
        external_id="local:report.json",
        recorded_at=report.generated_at + timedelta(minutes=4),
    )
    succeeded = await board.record_delivery_result(success)
    assert succeeded.status == DeliveryStatus.SUCCEEDED
    assert succeeded.attempt_count == 2
    assert await board.delivery_attempts(request.delivery_id) == [failure, success]


@pytest.mark.asyncio
async def test_delivery_state_machine_rejects_unsafe_transitions(run_id):
    board = InMemoryBlackboard()
    report = await board.put_run_report(make_report(run_id))
    request = make_request(report)
    await board.create_delivery(request)

    with pytest.raises(ValueError, match="invalid delivery transition"):
        await board.update_delivery(request.delivery_id, DeliveryStatus.SUCCEEDED)


def test_delivery_contracts_reject_raw_destinations_and_credential_errors(run_id):
    report = make_report(run_id)
    with pytest.raises(ValidationError):
        make_request(report, destination_id="https://hooks.example.test/secret")
    with pytest.raises(ValidationError, match="credential-like"):
        DeliveryError(
            code="connector.failed",
            category="unknown",
            retryable=False,
            redacted_message="Authorization: Bearer secret",
        )
