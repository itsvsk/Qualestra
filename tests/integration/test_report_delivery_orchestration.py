from __future__ import annotations

from uuid import UUID

from apps.control_api.service import RunService
from sentinelqa.blackboard.memory import InMemoryBlackboard
from sentinelqa.delivery import (
    DeliveryConnectorError,
    DeliveryReceipt,
    FilesystemConnector,
    build_delivery_registry_from_env,
)
from sentinelqa.domain.enums import DeliveryMode
from sentinelqa.domain.events import EventEnvelope
from sentinelqa.domain.models import AgentResult, RunReport, RunRequest
from sentinelqa.orchestration.agent_registry import AgentRegistry
from sentinelqa.orchestration.supervisor import DeterministicSupervisor


class FlakyConnector:
    connector_id = "flaky"

    def __init__(self) -> None:
        self.calls = 0

    async def deliver(
        self,
        report: RunReport,
        *,
        destination_id: str,
        idempotency_key: str,
    ) -> DeliveryReceipt:
        del report, destination_id
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("sensitive provider detail must not be persisted")
        return DeliveryReceipt(external_id=idempotency_key)


class ThrottledConnector:
    connector_id = "throttled"

    async def deliver(
        self,
        report: RunReport,
        *,
        destination_id: str,
        idempotency_key: str,
    ) -> DeliveryReceipt:
        del report, destination_id, idempotency_key
        raise DeliveryConnectorError(
            code="provider_rate_limited",
            category="transient",
            retryable=True,
            message="The configured provider rate-limited this delivery.",
        )


async def terminal_service() -> tuple[RunService, str]:
    blackboard = InMemoryBlackboard()
    registry = AgentRegistry.from_file("config/agents.yaml")
    service = RunService(DeterministicSupervisor(registry, blackboard))
    run = await service.create(RunRequest(target_id="atlas-local"))

    while task := await service.lease_task("report-test-worker"):
        summary = (
            {"charter": {"impacts": [], "task_slices": []}}
            if str(task.agent_type) == "change_impact"
            else {"completed": True}
        )
        await service.commit_result(
            AgentResult(
                task_id=task.task_id,
                agent_type=task.agent_type,
                outcome="succeeded",
                result_ref=f"blackboard://results/{task.task_id}",
                summary=summary,
            ),
            EventEnvelope(
                event_type="agent.result.committed",
                run_id=run.id,
                correlation_id=task.task_id,
                producer="report-test-worker",
                payload={"outcome": "succeeded"},
            ),
        )
    return service, str(run.id)


async def test_report_generation_is_stable_and_delivery_is_idempotent() -> None:
    service, run_id_text = await terminal_service()

    run_id = UUID(run_id_text)
    first = await service.report_bundle(run_id)
    second = await service.report_bundle(run_id, regenerate=True)

    assert first["documents"] == second["documents"]
    assert {item["format"] for item in first["documents"]} == {"json", "markdown", "html"}
    report_id = UUID(first["documents"][0]["report_id"])

    draft = await service.create_delivery(
        run_id=run_id,
        report_id=report_id,
        connector_id="noop",
        destination_id="local-noop",
        mode=DeliveryMode.DRY_RUN,
    )
    duplicate = await service.create_delivery(
        run_id=run_id,
        report_id=report_id,
        connector_id="noop",
        destination_id="local-noop",
        mode=DeliveryMode.DRY_RUN,
    )
    assert draft.status == "draft"
    assert duplicate.request.delivery_id == draft.request.delivery_id
    assert len(await service.deliveries(run_id)) == 1


async def test_manual_approval_and_failed_retry_preserve_attempt_history() -> None:
    service, run_id_text = await terminal_service()

    run_id = UUID(run_id_text)
    bundle = await service.report_bundle(run_id)
    report_id = UUID(bundle["documents"][0]["report_id"])

    pending = await service.create_delivery(
        run_id=run_id,
        report_id=report_id,
        connector_id="noop",
        destination_id="local-noop",
        mode=DeliveryMode.MANUAL,
    )
    assert pending.status == "pending_approval"
    approved = await service.approve_delivery(pending.request.delivery_id)
    assert approved["status"] == "succeeded"
    assert len(approved["attempts"]) == 1

    flaky = FlakyConnector()
    service.delivery_orchestrator.registry.register(flaky, "offline-flaky")
    failed = await service.create_delivery(
        run_id=run_id,
        report_id=report_id,
        connector_id="flaky",
        destination_id="offline-flaky",
        mode=DeliveryMode.AUTOMATIC,
    )
    assert failed.status == "failed"
    assert failed.last_error is not None
    assert "sensitive provider detail" not in failed.last_error.redacted_message

    retried = await service.retry_delivery(failed.request.delivery_id)
    assert retried["status"] == "succeeded"
    assert [item["status"] for item in retried["attempts"]] == ["failed", "succeeded"]


async def test_provider_failure_classification_is_preserved_without_raw_errors() -> None:
    service, run_id_text = await terminal_service()
    run_id = UUID(run_id_text)
    bundle = await service.report_bundle(run_id)
    report_id = UUID(bundle["documents"][0]["report_id"])
    service.delivery_orchestrator.registry.register(
        ThrottledConnector(),
        "offline-throttled",
        display_name="Offline throttled provider",
        kind="alert",
        provider="test",
    )

    failed = await service.create_delivery(
        run_id=run_id,
        report_id=report_id,
        connector_id="throttled",
        destination_id="offline-throttled",
        mode=DeliveryMode.AUTOMATIC,
    )

    assert failed.status == "failed"
    assert failed.last_error is not None
    assert failed.last_error.code == "provider_rate_limited"
    assert failed.last_error.category == "transient"
    assert failed.last_error.retryable is True


async def test_filesystem_connector_reuses_the_idempotent_target(tmp_path) -> None:
    service, run_id_text = await terminal_service()
    run_id = UUID(run_id_text)
    bundle = await service.report_bundle(run_id)
    report = await service.supervisor.blackboard.run_report(
        UUID(bundle["documents"][0]["report_id"])
    )
    assert report is not None
    connector = FilesystemConnector(tmp_path)

    first = await connector.deliver(
        report,
        destination_id="configured-local",
        idempotency_key="sha256:" + "a" * 64,
    )
    second = await connector.deliver(
        report,
        destination_id="configured-local",
        idempotency_key="sha256:" + "a" * 64,
    )

    assert first == second
    assert len(list(tmp_path.iterdir())) == 1
    assert next(tmp_path.iterdir()).read_text(encoding="utf-8") == report.content


def test_filesystem_delivery_defaults_to_the_persistent_artifact_root(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.delenv("SENTINEL_DELIVERY_DIR", raising=False)

    connector = build_delivery_registry_from_env().resolve("filesystem", "local-report")

    assert isinstance(connector, FilesystemConnector)
    assert connector.root == (tmp_path / "deliveries").resolve()


def test_delivery_catalog_contains_only_non_secret_metadata() -> None:
    catalog = build_delivery_registry_from_env().catalog()

    assert [(item.connector_id, item.destination_id) for item in catalog] == [
        ("filesystem", "local-report"),
        ("noop", "local-noop"),
    ]
    assert {item.kind for item in catalog} == {"artifact"}
    assert all("://" not in item.display_name for item in catalog)
