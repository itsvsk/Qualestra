from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sentinelqa.domain.enums import DeliveryStatus, TaskStatus
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

from .port import (
    PersistedState,
    initial_delivery_status,
    validate_delivery_transition,
)
from .product_graph import ProductGraph


class InMemoryBlackboard:
    """Transactional in-memory adapter used by tests and single-process local mode."""

    durable_leasing = False

    def __init__(self) -> None:
        self._runs: dict[UUID, Run] = {}
        self._tasks: dict[UUID, AgentTask] = {}
        self._contexts: dict[str, dict[str, Any]] = {}
        self._results: dict[UUID, AgentResult] = {}
        self._events: list[EventEnvelope] = []
        self._anomalies: dict[UUID, Anomaly] = {}
        self._defects: dict[UUID, Defect] = {}
        self._gates: dict[UUID, GateDecision] = {}
        self._reports: dict[UUID, RunReport] = {}
        self._report_keys: dict[tuple[UUID, str, str, str], UUID] = {}
        self._summaries: dict[UUID, ReleaseSummary] = {}
        self._summary_keys: dict[tuple[UUID, str], UUID] = {}
        self._deliveries: dict[UUID, DeliveryRecord] = {}
        self._delivery_keys: dict[str, UUID] = {}
        self._delivery_attempts: dict[UUID, dict[int, DeliveryResult]] = {}
        self._graphs: dict[UUID, ProductGraph] = {}
        self._lock = asyncio.Lock()

    async def put_context(self, key: str, value: dict[str, Any]) -> str:
        ref = f"blackboard://context/{key}"
        async with self._lock:
            if ref in self._contexts:
                if self._contexts[ref] != value:
                    raise ValueError("context references are immutable")
            else:
                self._contexts[ref] = deepcopy(value)
        return ref

    async def get_context(self, ref: str) -> dict[str, Any]:
        async with self._lock:
            return deepcopy(self._contexts[ref])

    async def commit_result(self, result: AgentResult, event: EventEnvelope) -> None:
        async with self._lock:
            existing = self._results.get(result.task_id)
            if existing and existing != result:
                raise ValueError("task result is immutable")
            self._results[result.task_id] = deepcopy(result)
            self._events.append(deepcopy(event))
            if result.task_id in self._tasks:
                status = (
                    TaskStatus.SUCCEEDED if result.outcome == "succeeded" else TaskStatus.FAILED
                )
                self._tasks[result.task_id] = self._tasks[result.task_id].model_copy(
                    update={"status": status}
                )

    async def result(self, task_id: UUID) -> AgentResult | None:
        async with self._lock:
            return deepcopy(self._results.get(task_id))

    async def append_event(self, event: EventEnvelope) -> None:
        async with self._lock:
            self._events.append(deepcopy(event))

    async def events(self, run_id: UUID) -> list[EventEnvelope]:
        async with self._lock:
            return deepcopy([event for event in self._events if event.run_id == run_id])

    async def put_anomaly(self, anomaly: Anomaly) -> None:
        async with self._lock:
            self._anomalies.setdefault(anomaly.id, deepcopy(anomaly))

    async def anomalies(self, run_id: UUID) -> list[Anomaly]:
        async with self._lock:
            return deepcopy([item for item in self._anomalies.values() if item.run_id == run_id])

    async def put_defect(self, defect: Defect) -> None:
        async with self._lock:
            self._defects.setdefault(defect.defect_id, deepcopy(defect))

    async def defects(self, run_id: UUID) -> list[Defect]:
        async with self._lock:
            return deepcopy([item for item in self._defects.values() if item.run_id == run_id])

    async def put_gate(self, gate: GateDecision) -> None:
        async with self._lock:
            self._gates[gate.run_id] = deepcopy(gate)

    async def gate(self, run_id: UUID) -> GateDecision | None:
        async with self._lock:
            return deepcopy(self._gates.get(run_id))

    async def put_run_report(self, report: RunReport) -> RunReport:
        key = (report.run_id, report.source_fingerprint, str(report.kind), str(report.format))
        async with self._lock:
            existing_id = self._report_keys.get(key)
            if existing_id is not None:
                existing = self._reports[existing_id]
                if existing.digest != report.digest:
                    raise ValueError("report source fingerprint is already bound to other content")
                return deepcopy(existing)
            if report.report_id in self._reports and self._reports[report.report_id] != report:
                raise ValueError("report id is already registered with different data")
            self._reports[report.report_id] = deepcopy(report)
            self._report_keys[key] = report.report_id
            return deepcopy(report)

    async def run_report(self, report_id: UUID) -> RunReport | None:
        async with self._lock:
            return deepcopy(self._reports.get(report_id))

    async def reports(self, run_id: UUID) -> list[RunReport]:
        async with self._lock:
            reports = [item for item in self._reports.values() if item.run_id == run_id]
            return deepcopy(sorted(reports, key=lambda item: (item.generated_at, item.report_id)))

    async def put_release_summary(self, summary: ReleaseSummary) -> ReleaseSummary:
        key = (summary.run_id, summary.source_fingerprint)
        async with self._lock:
            existing_id = self._summary_keys.get(key)
            if existing_id is not None:
                existing = self._summaries[existing_id]
                if existing != summary:
                    raise ValueError(
                        "summary source fingerprint is already bound to different facts"
                    )
                return deepcopy(existing)
            report = self._reports.get(summary.report_id)
            if report is None:
                raise ValueError("release summary references an unknown report")
            if report.run_id != summary.run_id:
                raise ValueError("release summary report belongs to another run")
            self._summaries[summary.summary_id] = deepcopy(summary)
            self._summary_keys[key] = summary.summary_id
            return deepcopy(summary)

    async def release_summary(self, run_id: UUID) -> ReleaseSummary | None:
        async with self._lock:
            summaries = [item for item in self._summaries.values() if item.run_id == run_id]
            if not summaries:
                return None
            return deepcopy(max(summaries, key=lambda item: (item.generated_at, item.summary_id)))

    async def create_delivery(
        self, request: DeliveryRequest, status: DeliveryStatus | None = None
    ) -> DeliveryRecord:
        async with self._lock:
            existing_id = self._delivery_keys.get(request.idempotency_key)
            if existing_id is not None:
                return deepcopy(self._deliveries[existing_id])
            if request.delivery_id in self._deliveries:
                existing = self._deliveries[request.delivery_id]
                if existing.request != request:
                    raise ValueError("delivery id is already registered with different data")
                return deepcopy(existing)
            report = self._reports.get(request.report_id)
            if (
                report is None
                or report.run_id != request.run_id
                or report.digest != request.report_digest
                or report.format != request.report_format
            ):
                raise ValueError("delivery request must reference a persisted matching report")
            now = request.created_at
            record = DeliveryRecord(
                request=request,
                status=status or initial_delivery_status(request),
                updated_at=now,
            )
            self._deliveries[request.delivery_id] = record
            self._delivery_keys[request.idempotency_key] = request.delivery_id
            return deepcopy(record)

    async def delivery(self, delivery_id: UUID) -> DeliveryRecord | None:
        async with self._lock:
            return deepcopy(self._deliveries.get(delivery_id))

    async def deliveries(self, run_id: UUID) -> list[DeliveryRecord]:
        async with self._lock:
            records = [
                record for record in self._deliveries.values() if record.request.run_id == run_id
            ]
            return deepcopy(
                sorted(
                    records, key=lambda item: (item.request.created_at, item.request.delivery_id)
                )
            )

    async def update_delivery(
        self,
        delivery_id: UUID,
        status: DeliveryStatus,
        *,
        last_error: DeliveryError | None = None,
        approved_at: datetime | None = None,
        next_attempt_at: datetime | None = None,
    ) -> DeliveryRecord:
        async with self._lock:
            current = self._deliveries[delivery_id]
            validate_delivery_transition(DeliveryStatus(current.status), status)
            effective_error = (
                current.last_error
                if status == current.status and last_error is None
                else last_error
            )
            record = current.model_copy(
                update={
                    "status": status,
                    "last_error": effective_error,
                    "approved_at": approved_at or current.approved_at,
                    "next_attempt_at": next_attempt_at,
                    "updated_at": datetime.now(UTC),
                }
            )
            record = DeliveryRecord.model_validate(record.model_dump())
            self._deliveries[delivery_id] = record
            return deepcopy(record)

    async def claim_delivery(self, delivery_id: UUID) -> DeliveryRecord | None:
        async with self._lock:
            current = self._deliveries.get(delivery_id)
            if current is None or current.status != DeliveryStatus.QUEUED:
                return None
            record = current.model_copy(
                update={
                    "status": DeliveryStatus.PROCESSING,
                    "last_error": None,
                    "next_attempt_at": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._deliveries[delivery_id] = record
            return deepcopy(record)

    async def record_delivery_result(self, result: DeliveryResult) -> DeliveryRecord:
        async with self._lock:
            current = self._deliveries[result.delivery_id]
            existing = self._delivery_attempts.setdefault(result.delivery_id, {}).get(
                result.attempt
            )
            if existing is not None:
                if existing != result:
                    raise ValueError("delivery attempt is immutable")
                return deepcopy(current)
            if current.status != DeliveryStatus.PROCESSING:
                raise ValueError("delivery results can only complete processing deliveries")
            if result.connector_id != current.request.connector_id:
                raise ValueError("delivery result connector does not match its request")
            if result.attempt != current.attempt_count + 1:
                raise ValueError("delivery attempts must be recorded sequentially")
            status = (
                DeliveryStatus.SUCCEEDED if result.status == "succeeded" else DeliveryStatus.FAILED
            )
            record = current.model_copy(
                update={
                    "status": status,
                    "attempt_count": result.attempt,
                    "last_error": result.error,
                    "updated_at": result.recorded_at,
                }
            )
            record = DeliveryRecord.model_validate(record.model_dump())
            self._delivery_attempts[result.delivery_id][result.attempt] = deepcopy(result)
            self._deliveries[result.delivery_id] = record
            return deepcopy(record)

    async def delivery_attempts(self, delivery_id: UUID) -> list[DeliveryResult]:
        async with self._lock:
            attempts = self._delivery_attempts.get(delivery_id, {})
            return deepcopy([attempts[number] for number in sorted(attempts)])

    async def register_run(self, run: Run, tasks: list[AgentTask]) -> None:
        async with self._lock:
            if run.id in self._runs and self._runs[run.id] != run:
                raise ValueError("run is already registered with different data")
            self._runs.setdefault(run.id, deepcopy(run))
            for task in tasks:
                self._tasks.setdefault(task.task_id, deepcopy(task))

    async def register_tasks(self, tasks: list[AgentTask]) -> None:
        async with self._lock:
            for task in tasks:
                self._tasks.setdefault(task.task_id, deepcopy(task))

    async def update_task_status(self, task_id: UUID, status: TaskStatus) -> None:
        async with self._lock:
            self._tasks[task_id] = self._tasks[task_id].model_copy(update={"status": status})

    async def update_run(self, run: Run) -> None:
        async with self._lock:
            self._runs[run.id] = deepcopy(run)

    async def load_state(self) -> PersistedState:
        async with self._lock:
            return PersistedState(
                runs=tuple(deepcopy(list(self._runs.values()))),
                tasks=tuple(deepcopy(list(self._tasks.values()))),
                results=tuple(deepcopy(list(self._results.values()))),
                anomalies=tuple(deepcopy(list(self._anomalies.values()))),
                defects=tuple(deepcopy(list(self._defects.values()))),
                gates=tuple(deepcopy(list(self._gates.values()))),
                reports=tuple(deepcopy(list(self._reports.values()))),
                summaries=tuple(deepcopy(list(self._summaries.values()))),
                deliveries=tuple(deepcopy(list(self._deliveries.values()))),
            )

    async def recover_expired_tasks(self) -> tuple[UUID, ...]:
        return ()

    async def lease_task(
        self,
        worker: str,
        agent_types: tuple[str, ...] = (),
        partition_prefixes: tuple[str, ...] = (),
    ) -> AgentTask | None:
        del worker, agent_types, partition_prefixes
        return None

    async def put_observation(self, observation: Observation) -> None:
        async with self._lock:
            self._graphs.setdefault(observation.run_id, ProductGraph()).observe(observation)

    async def put_transition(self, run_id: UUID, transition: Transition) -> None:
        async with self._lock:
            graph = self._graphs.setdefault(run_id, ProductGraph())
            key = (transition.from_state_id, str(transition.action_id), transition.to_state_id)
            graph.transitions.setdefault(key, deepcopy(transition))

    async def coverage(self, run_id: UUID) -> dict[str, Any]:
        async with self._lock:
            graph = self._graphs.get(run_id)
            if graph is None:
                return {
                    "states": 0,
                    "transitions": 0,
                    "routes": 0,
                    "roles": 0,
                    "graph": {"nodes": [], "edges": []},
                }
            metrics = graph.coverage()
            return {
                **metrics,
                "graph": {
                    "nodes": [
                        {
                            "id": node.fingerprint,
                            "route": node.route,
                            "role": node.role,
                            "visited_count": node.visited_count,
                        }
                        for node in graph.nodes.values()
                    ],
                    "edges": [item.model_dump(mode="json") for item in graph.transitions.values()],
                },
            }
