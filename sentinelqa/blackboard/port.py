from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from sentinelqa.domain.enums import DeliveryMode, DeliveryStatus, TaskStatus
from sentinelqa.domain.events import EventEnvelope
from sentinelqa.domain.models import (
    AgentResult,
    AgentTask,
    Anomaly,
    Defect,
    DeliveryError,
    DeliveryRecord,
    DeliveryRequest,
    DeliveryResult,
    GateDecision,
    Observation,
    ReleaseSummary,
    Run,
    RunReport,
    Transition,
)


@dataclass(frozen=True, slots=True)
class PersistedState:
    runs: tuple[Run, ...] = ()
    tasks: tuple[AgentTask, ...] = ()
    results: tuple[AgentResult, ...] = ()
    anomalies: tuple[Anomaly, ...] = ()
    defects: tuple[Defect, ...] = ()
    gates: tuple[GateDecision, ...] = ()
    reports: tuple[RunReport, ...] = ()
    summaries: tuple[ReleaseSummary, ...] = ()
    deliveries: tuple[DeliveryRecord, ...] = ()


DELIVERY_TRANSITIONS: dict[DeliveryStatus, frozenset[DeliveryStatus]] = {
    DeliveryStatus.DRAFT: frozenset({DeliveryStatus.PENDING_APPROVAL, DeliveryStatus.QUEUED}),
    DeliveryStatus.PENDING_APPROVAL: frozenset({DeliveryStatus.QUEUED}),
    DeliveryStatus.QUEUED: frozenset({DeliveryStatus.PROCESSING}),
    DeliveryStatus.PROCESSING: frozenset({DeliveryStatus.SUCCEEDED, DeliveryStatus.FAILED}),
    DeliveryStatus.FAILED: frozenset({DeliveryStatus.QUEUED}),
    DeliveryStatus.SUCCEEDED: frozenset(),
}


def initial_delivery_status(request: DeliveryRequest) -> DeliveryStatus:
    if request.mode == DeliveryMode.DRY_RUN:
        return DeliveryStatus.DRAFT
    if request.mode == DeliveryMode.MANUAL:
        return DeliveryStatus.PENDING_APPROVAL
    return DeliveryStatus.QUEUED


def validate_delivery_transition(current: DeliveryStatus, target: DeliveryStatus) -> None:
    if current == target:
        return
    if target not in DELIVERY_TRANSITIONS[current]:
        raise ValueError(f"invalid delivery transition: {current} -> {target}")


class Blackboard(Protocol):
    durable_leasing: bool

    async def put_context(self, key: str, value: dict[str, Any]) -> str: ...

    async def get_context(self, ref: str) -> dict[str, Any]: ...

    async def commit_result(self, result: AgentResult, event: EventEnvelope) -> None: ...

    async def result(self, task_id: UUID) -> AgentResult | None: ...

    async def append_event(self, event: EventEnvelope) -> None: ...

    async def events(self, run_id: UUID) -> list[EventEnvelope]: ...

    async def put_anomaly(self, anomaly: Anomaly) -> None: ...

    async def anomalies(self, run_id: UUID) -> list[Anomaly]: ...

    async def put_defect(self, defect: Defect) -> None: ...

    async def defects(self, run_id: UUID) -> list[Defect]: ...

    async def put_gate(self, gate: GateDecision) -> None: ...

    async def gate(self, run_id: UUID) -> GateDecision | None: ...

    async def put_run_report(self, report: RunReport) -> RunReport: ...

    async def run_report(self, report_id: UUID) -> RunReport | None: ...

    async def reports(self, run_id: UUID) -> list[RunReport]: ...

    async def put_release_summary(self, summary: ReleaseSummary) -> ReleaseSummary: ...

    async def release_summary(self, run_id: UUID) -> ReleaseSummary | None: ...

    async def create_delivery(
        self, request: DeliveryRequest, status: DeliveryStatus | None = None
    ) -> DeliveryRecord: ...

    async def delivery(self, delivery_id: UUID) -> DeliveryRecord | None: ...

    async def deliveries(self, run_id: UUID) -> list[DeliveryRecord]: ...

    async def update_delivery(
        self,
        delivery_id: UUID,
        status: DeliveryStatus,
        *,
        last_error: DeliveryError | None = None,
        approved_at: datetime | None = None,
        next_attempt_at: datetime | None = None,
    ) -> DeliveryRecord: ...

    async def claim_delivery(self, delivery_id: UUID) -> DeliveryRecord | None: ...

    async def record_delivery_result(self, result: DeliveryResult) -> DeliveryRecord: ...

    async def delivery_attempts(self, delivery_id: UUID) -> list[DeliveryResult]: ...

    async def register_run(self, run: Run, tasks: list[AgentTask]) -> None: ...

    async def register_tasks(self, tasks: list[AgentTask]) -> None: ...

    async def update_task_status(self, task_id: UUID, status: TaskStatus) -> None: ...

    async def update_run(self, run: Run) -> None: ...

    async def load_state(self) -> PersistedState: ...

    async def recover_expired_tasks(self) -> tuple[UUID, ...]: ...

    async def lease_task(
        self,
        worker: str,
        agent_types: tuple[str, ...] = (),
        partition_prefixes: tuple[str, ...] = (),
    ) -> AgentTask | None: ...

    async def put_observation(self, observation: Observation) -> None: ...

    async def put_transition(self, run_id: UUID, transition: Transition) -> None: ...

    async def coverage(self, run_id: UUID) -> dict[str, Any]: ...
