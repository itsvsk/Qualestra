import json
from pathlib import Path

from jsonschema import Draft202012Validator

from sentinelqa.domain.enums import (
    AgentType,
    DeliveryMode,
    ReportFormat,
    ReportKind,
    RunStatus,
)
from sentinelqa.domain.events import EventEnvelope
from sentinelqa.domain.models import (
    AgentResult,
    DeliveryError,
    DeliveryRequest,
    DeliveryResult,
    ReleaseSummary,
    RunReport,
    stable_digest,
)


def schemas():
    return sorted(Path("contracts").glob("*.schema.json"))


def test_all_contracts_are_valid_draft_2020_12_schemas():
    assert len(schemas()) >= 10
    for path in schemas():
        Draft202012Validator.check_schema(json.loads(path.read_text()))


def test_result_and_event_examples_validate(task_factory):
    task = task_factory(AgentType.CHANGE_IMPACT)
    result = AgentResult(
        task_id=task.task_id,
        agent_type=AgentType.CHANGE_IMPACT,
        outcome="succeeded",
        result_ref=f"blackboard://results/{task.task_id}",
    )
    event = EventEnvelope(
        event_type="agent.result.committed",
        run_id=task.run_id,
        correlation_id=task.task_id,
        producer="qa-worker",
    )
    result_schema = json.loads(Path("contracts/agent-result.schema.json").read_text())
    event_schema = json.loads(Path("contracts/event.schema.json").read_text())
    assert not list(Draft202012Validator(result_schema).iter_errors(result.model_dump(mode="json")))
    assert not list(Draft202012Validator(event_schema).iter_errors(event.model_dump(mode="json")))


def test_report_summary_and_delivery_examples_validate(run_id):
    generated_at = "2026-07-27T10:00:00Z"
    content = '{"outcome":"BLOCK"}'
    report = RunReport(
        run_id=run_id,
        kind=ReportKind.GATE,
        format=ReportFormat.JSON,
        content=content,
        digest=stable_digest(content),
        source_fingerprint=stable_digest(run_id, "committed-evidence"),
        generated_at=generated_at,
    )
    summary = ReleaseSummary(
        run_id=run_id,
        report_id=report.report_id,
        run_status=RunStatus.BLOCKED,
        gate_outcome="BLOCK",
        verified_defects=1,
        defects_by_severity={"low": 0, "medium": 0, "high": 1, "critical": 0},
        coverage_metrics={"routes": 4, "journey_ratio": 0.8},
        blockers=("verified high defect",),
        release_confidence=0.95,
        source_fingerprint=report.source_fingerprint,
        generated_at=generated_at,
    )
    request = DeliveryRequest(
        run_id=run_id,
        report_id=report.report_id,
        report_digest=report.digest,
        report_format=report.format,
        connector_id="filesystem",
        destination_id="local-audit",
        mode=DeliveryMode.MANUAL,
        idempotency_key=DeliveryRequest.stable_key(
            run_id,
            report.digest,
            "filesystem",
            "local-audit",
            DeliveryMode.MANUAL,
        ),
        created_at=generated_at,
    )
    result = DeliveryResult(
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
        recorded_at=generated_at,
    )

    examples = {
        "run-report.schema.json": report,
        "release-summary.schema.json": summary,
        "delivery-request.schema.json": request,
        "delivery-result.schema.json": result,
    }
    for filename, model in examples.items():
        schema = json.loads(Path("contracts", filename).read_text())
        errors = list(Draft202012Validator(schema).iter_errors(model.model_dump(mode="json")))
        assert not errors


def test_report_and_delivery_events_validate(run_id):
    schema = json.loads(Path("contracts/event.schema.json").read_text())
    validator = Draft202012Validator(schema)
    for event_type in ("report.generated", "report.regenerated", "delivery.retried"):
        event = EventEnvelope(
            event_type=event_type,
            run_id=run_id,
            correlation_id=run_id,
            producer="qa-worker",
        )
        assert not list(validator.iter_errors(event.model_dump(mode="json")))
