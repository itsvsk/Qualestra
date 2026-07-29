from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import UUID

import httpx

from apps.control_api.service import RunService
from sentinelqa.blackboard.memory import InMemoryBlackboard
from sentinelqa.delivery.configuration import (
    DeliveryProviderConfiguration,
    register_provider_destinations,
)
from sentinelqa.delivery.providers import ProviderHttpResponse
from sentinelqa.domain.enums import DeliveryMode
from sentinelqa.domain.events import EventEnvelope
from sentinelqa.domain.models import AgentResult, RunRequest
from sentinelqa.orchestration.agent_registry import AgentRegistry
from sentinelqa.orchestration.supervisor import DeterministicSupervisor


class TicketTransport:
    def __init__(self, *responses: ProviderHttpResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, object] | None = None,
    ) -> ProviderHttpResponse:
        del headers, json_body
        self.calls.append((method, url))
        return self.responses.pop(0)


async def terminal_service() -> tuple[RunService, UUID]:
    blackboard = InMemoryBlackboard()
    registry = AgentRegistry.from_file("config/agents.yaml")
    service = RunService(DeterministicSupervisor(registry, blackboard))
    run = await service.create(RunRequest(target_id="atlas-local"))
    while task := await service.lease_task("provider-test-worker"):
        summary: dict[str, object] = (
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
                producer="provider-test-worker",
                payload={"outcome": "succeeded"},
            ),
        )
    return service, run.id


async def test_manual_github_ticket_uses_existing_outbox_approval_and_idempotency() -> None:
    service, run_id = await terminal_service()
    transport = TicketTransport(
        ProviderHttpResponse(200, {"total_count": 0, "items": []}),
        ProviderHttpResponse(201, {"number": 42, "node_id": "I_SAFE"}),
    )
    configuration = DeliveryProviderConfiguration.model_validate(
        {
            "destinations": [
                {
                    "provider": "github",
                    "destination_id": "release-defects",
                    "display_name": "Release defects",
                    "repository": "example/project",
                    "token_env": "SENTINEL_GITHUB_TOKEN",
                }
            ]
        }
    )
    register_provider_destinations(
        service.delivery_orchestrator.registry,
        configuration,
        environment={"SENTINEL_GITHUB_TOKEN": "redaction-sentinel"},
        ticket_transport=transport,
    )
    report = await service.report_bundle(run_id)
    report_id = UUID(
        next(item["report_id"] for item in report["documents"] if item["format"] == "json")
    )

    pending = await service.create_delivery(
        run_id=run_id,
        report_id=report_id,
        connector_id="github_issues",
        destination_id="release-defects",
        mode=DeliveryMode.MANUAL,
    )
    assert pending.status == "pending_approval"
    assert transport.calls == []

    approved = await service.approve_delivery(pending.request.delivery_id)
    duplicate = await service.create_delivery(
        run_id=run_id,
        report_id=report_id,
        connector_id="github_issues",
        destination_id="release-defects",
        mode=DeliveryMode.MANUAL,
    )

    assert approved["status"] == "succeeded"
    assert approved["external_reference"] == "I_SAFE"
    assert approved["external_url"] == "https://github.com/example/project/issues/42"
    assert duplicate.request.delivery_id == pending.request.delivery_id
    assert [method for method, _ in transport.calls] == ["GET", "POST"]


async def test_slack_rate_limit_retries_with_the_same_client_message_id() -> None:
    service, run_id = await terminal_service()
    payloads: list[dict[str, object]] = []

    def slack_handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(429, json={"ok": False, "error": "ratelimited"})
        return httpx.Response(200, json={"ok": True, "ts": "1712345678.000100"})

    configuration = DeliveryProviderConfiguration.model_validate(
        {
            "destinations": [
                {
                    "provider": "slack",
                    "destination_id": "release-alerts",
                    "display_name": "Release alerts",
                    "channel": "C0123456789",
                    "token_env": "SENTINEL_SLACK_TOKEN",
                }
            ]
        }
    )
    register_provider_destinations(
        service.delivery_orchestrator.registry,
        configuration,
        environment={"SENTINEL_SLACK_TOKEN": "redaction-sentinel"},
        slack_transport=httpx.MockTransport(slack_handler),
    )
    report = await service.report_bundle(run_id)
    report_id = UUID(
        next(item["report_id"] for item in report["documents"] if item["format"] == "json")
    )

    failed = await service.create_delivery(
        run_id=run_id,
        report_id=report_id,
        connector_id="slack",
        destination_id="release-alerts",
        mode=DeliveryMode.AUTOMATIC,
    )
    assert failed.status == "failed"
    assert failed.last_error is not None
    assert failed.last_error.code == "slack.rate_limited"

    retried = await service.retry_delivery(failed.request.delivery_id)

    assert retried["status"] == "succeeded"
    assert [attempt["status"] for attempt in retried["attempts"]] == ["failed", "succeeded"]
    assert payloads[0]["client_msg_id"] == payloads[1]["client_msg_id"]
