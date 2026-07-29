from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    RunRequest,
    Transition,
)
from sentinelqa.storage.database import (
    AgentResultRow,
    AgentTaskRow,
    AnomalyRow,
    ContextRow,
    DefectRow,
    DeliveryAttemptRow,
    DeliveryRow,
    EventRow,
    GateRow,
    ProductStateRow,
    ProductTransitionRow,
    ReleaseSummaryRow,
    RunReportRow,
    RunRow,
)

from .port import PersistedState, initial_delivery_status, validate_delivery_transition


def task_row(task: AgentTask) -> AgentTaskRow:
    return AgentTaskRow(
        id=task.task_id,
        run_id=task.run_id,
        parent_task_id=task.parent_task_id,
        agent_type=str(task.agent_type),
        agent_version=task.agent_version,
        status=str(task.status),
        dependency_task_ids=[str(item) for item in task.dependency_task_ids],
        input_ref=task.input_ref,
        idempotency_key=task.idempotency_key,
        target_partition=task.target_partition,
        budget=task.budget.model_dump(mode="json"),
        deadline=task.deadline,
        attempt=task.attempt,
    )


def agent_task(row: AgentTaskRow) -> AgentTask:
    return AgentTask(
        task_id=row.id,
        run_id=row.run_id,
        parent_task_id=row.parent_task_id,
        agent_type=row.agent_type,
        agent_version=row.agent_version,
        status=row.status,
        dependency_task_ids=tuple(UUID(item) for item in row.dependency_task_ids),
        input_ref=row.input_ref,
        idempotency_key=row.idempotency_key,
        target_partition=row.target_partition,
        budget=row.budget,
        deadline=row.deadline,
        attempt=row.attempt,
    )


def delivery_record(row: DeliveryRow) -> DeliveryRecord:
    return DeliveryRecord(
        request=DeliveryRequest.model_validate(row.request_payload),
        status=row.status,
        attempt_count=row.attempt_count,
        last_error=DeliveryError.model_validate(row.last_error) if row.last_error else None,
        approved_at=row.approved_at,
        next_attempt_at=row.next_attempt_at,
        updated_at=row.updated_at,
    )


class PostgresBlackboard:
    durable_leasing = True

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def put_context(self, key: str, value: dict[str, Any]) -> str:
        ref = f"blackboard://context/{key}"
        async with self.sessions.begin() as session:
            existing = await session.get(ContextRow, ref)
            if existing is not None and existing.payload != value:
                raise ValueError("context references are immutable")
            if existing is None:
                session.add(ContextRow(ref=ref, payload=value))
        return ref

    async def get_context(self, ref: str) -> dict[str, Any]:
        async with self.sessions() as session:
            row = await session.get(ContextRow, ref)
            if row is None:
                raise KeyError(ref)
            return dict(row.payload)

    async def commit_result(self, result: AgentResult, event: EventEnvelope) -> None:
        payload = result.model_dump(mode="json")
        async with self.sessions.begin() as session:
            existing = await session.get(AgentResultRow, result.task_id)
            if existing is not None and existing.payload != payload:
                raise ValueError("task result is immutable")
            if existing is None:
                session.add(
                    AgentResultRow(
                        task_id=result.task_id,
                        schema_version=result.schema_version,
                        outcome=result.outcome,
                        result_ref=result.result_ref,
                        payload=payload,
                        produced_at=result.produced_at,
                    )
                )
                session.add(self._event_row(event))
                status = (
                    TaskStatus.SUCCEEDED if result.outcome == "succeeded" else TaskStatus.FAILED
                )
                await session.execute(
                    update(AgentTaskRow)
                    .where(AgentTaskRow.id == result.task_id)
                    .values(status=str(status), lease_owner=None, lease_expires_at=None)
                )

    async def result(self, task_id: UUID) -> AgentResult | None:
        async with self.sessions() as session:
            row = await session.get(AgentResultRow, task_id)
            return AgentResult.model_validate(row.payload) if row is not None else None

    async def append_event(self, event: EventEnvelope) -> None:
        async with self.sessions.begin() as session:
            if await session.get(EventRow, event.event_id) is None:
                session.add(self._event_row(event))

    async def events(self, run_id: UUID) -> list[EventEnvelope]:
        async with self.sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(EventRow)
                        .where(EventRow.run_id == run_id)
                        .order_by(EventRow.occurred_at, EventRow.id)
                    )
                ).all()
            )
        return [
            EventEnvelope(
                event_id=row.id,
                event_type=row.event_type,
                occurred_at=row.occurred_at,
                run_id=row.run_id,
                correlation_id=row.correlation_id,
                producer=row.producer,
                payload=row.payload,
            )
            for row in rows
        ]

    async def put_anomaly(self, anomaly: Anomaly) -> None:
        async with self.sessions.begin() as session:
            if await session.get(AnomalyRow, anomaly.id) is None:
                session.add(
                    AnomalyRow(
                        id=anomaly.id,
                        run_id=anomaly.run_id,
                        signature=anomaly.signature,
                        payload=anomaly.model_dump(mode="json"),
                    )
                )

    async def anomalies(self, run_id: UUID) -> list[Anomaly]:
        async with self.sessions() as session:
            rows = list(
                (await session.scalars(select(AnomalyRow).where(AnomalyRow.run_id == run_id))).all()
            )
        return [Anomaly.model_validate(row.payload) for row in rows]

    async def put_defect(self, defect: Defect) -> None:
        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(DefectRow).where(
                    DefectRow.run_id == defect.run_id, DefectRow.signature == defect.signature
                )
            )
            if existing is None:
                session.add(
                    DefectRow(
                        id=defect.defect_id,
                        run_id=defect.run_id,
                        signature=defect.signature,
                        payload=defect.model_dump(mode="json"),
                    )
                )

    async def defects(self, run_id: UUID) -> list[Defect]:
        async with self.sessions() as session:
            rows = list(
                (await session.scalars(select(DefectRow).where(DefectRow.run_id == run_id))).all()
            )
        return [Defect.model_validate(row.payload) for row in rows]

    async def put_gate(self, gate: GateDecision) -> None:
        async with self.sessions.begin() as session:
            statement = pg_insert(GateRow).values(
                run_id=gate.run_id,
                payload=gate.model_dump(mode="json"),
            )
            await session.execute(
                statement.on_conflict_do_update(
                    index_elements=[GateRow.run_id],
                    set_={"payload": statement.excluded.payload},
                )
            )

    async def gate(self, run_id: UUID) -> GateDecision | None:
        async with self.sessions() as session:
            row = await session.get(GateRow, run_id)
            return GateDecision.model_validate(row.payload) if row is not None else None

    async def put_run_report(self, report: RunReport) -> RunReport:
        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(RunReportRow).where(
                    RunReportRow.run_id == report.run_id,
                    RunReportRow.source_fingerprint == report.source_fingerprint,
                    RunReportRow.kind == str(report.kind),
                    RunReportRow.format == str(report.format),
                )
            )
            if existing is not None:
                persisted = RunReport.model_validate(existing.payload)
                if persisted.digest != report.digest:
                    raise ValueError("report source fingerprint is already bound to other content")
                return persisted
            by_id = await session.get(RunReportRow, report.report_id)
            if by_id is not None:
                persisted = RunReport.model_validate(by_id.payload)
                if persisted != report:
                    raise ValueError("report id is already registered with different data")
                return persisted
            session.add(
                RunReportRow(
                    id=report.report_id,
                    run_id=report.run_id,
                    schema_version=report.schema_version,
                    kind=str(report.kind),
                    format=str(report.format),
                    digest=report.digest,
                    source_fingerprint=report.source_fingerprint,
                    payload=report.model_dump(mode="json"),
                    generated_at=report.generated_at,
                )
            )
            return report

    async def run_report(self, report_id: UUID) -> RunReport | None:
        async with self.sessions() as session:
            row = await session.get(RunReportRow, report_id)
            return RunReport.model_validate(row.payload) if row is not None else None

    async def reports(self, run_id: UUID) -> list[RunReport]:
        async with self.sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(RunReportRow)
                        .where(RunReportRow.run_id == run_id)
                        .order_by(RunReportRow.generated_at, RunReportRow.id)
                    )
                ).all()
            )
        return [RunReport.model_validate(row.payload) for row in rows]

    async def put_release_summary(self, summary: ReleaseSummary) -> ReleaseSummary:
        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(ReleaseSummaryRow).where(
                    ReleaseSummaryRow.run_id == summary.run_id,
                    ReleaseSummaryRow.source_fingerprint == summary.source_fingerprint,
                )
            )
            if existing is not None:
                persisted = ReleaseSummary.model_validate(existing.payload)
                if persisted != summary:
                    raise ValueError(
                        "summary source fingerprint is already bound to different facts"
                    )
                return persisted
            report = await session.get(RunReportRow, summary.report_id)
            if report is None:
                raise ValueError("release summary references an unknown report")
            if report.run_id != summary.run_id:
                raise ValueError("release summary report belongs to another run")
            session.add(
                ReleaseSummaryRow(
                    id=summary.summary_id,
                    run_id=summary.run_id,
                    report_id=summary.report_id,
                    schema_version=summary.schema_version,
                    source_fingerprint=summary.source_fingerprint,
                    payload=summary.model_dump(mode="json"),
                    generated_at=summary.generated_at,
                )
            )
            return summary

    async def release_summary(self, run_id: UUID) -> ReleaseSummary | None:
        async with self.sessions() as session:
            row = await session.scalar(
                select(ReleaseSummaryRow)
                .where(ReleaseSummaryRow.run_id == run_id)
                .order_by(ReleaseSummaryRow.generated_at.desc(), ReleaseSummaryRow.id.desc())
                .limit(1)
            )
            return ReleaseSummary.model_validate(row.payload) if row is not None else None

    async def create_delivery(
        self, request: DeliveryRequest, status: DeliveryStatus | None = None
    ) -> DeliveryRecord:
        chosen_status = status or initial_delivery_status(request)
        record = DeliveryRecord(
            request=request,
            status=chosen_status,
            updated_at=request.created_at,
        )
        values = {
            "id": request.delivery_id,
            "run_id": request.run_id,
            "report_id": request.report_id,
            "connector_id": request.connector_id,
            "destination_id": request.destination_id,
            "mode": str(request.mode),
            "status": str(chosen_status),
            "idempotency_key": request.idempotency_key,
            "request_payload": request.model_dump(mode="json"),
            "attempt_count": 0,
            "last_error": None,
            "approved_at": None,
            "next_attempt_at": None,
            "created_at": request.created_at,
            "updated_at": request.created_at,
        }
        async with self.sessions.begin() as session:
            report = await session.get(RunReportRow, request.report_id)
            if (
                report is None
                or report.run_id != request.run_id
                or report.digest != request.report_digest
                or report.format != request.report_format
            ):
                raise ValueError("delivery request must reference a persisted matching report")
            statement = (
                pg_insert(DeliveryRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=[DeliveryRow.idempotency_key])
                .returning(DeliveryRow.id)
            )
            inserted_id = await session.scalar(statement)
            if inserted_id is not None:
                return record
            existing = await session.scalar(
                select(DeliveryRow).where(DeliveryRow.idempotency_key == request.idempotency_key)
            )
            if existing is None:
                raise RuntimeError("delivery idempotency conflict did not resolve")
            return delivery_record(existing)

    async def delivery(self, delivery_id: UUID) -> DeliveryRecord | None:
        async with self.sessions() as session:
            row = await session.get(DeliveryRow, delivery_id)
            return delivery_record(row) if row is not None else None

    async def deliveries(self, run_id: UUID) -> list[DeliveryRecord]:
        async with self.sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(DeliveryRow)
                        .where(DeliveryRow.run_id == run_id)
                        .order_by(DeliveryRow.created_at, DeliveryRow.id)
                    )
                ).all()
            )
        return [delivery_record(row) for row in rows]

    async def update_delivery(
        self,
        delivery_id: UUID,
        status: DeliveryStatus,
        *,
        last_error: DeliveryError | None = None,
        approved_at: datetime | None = None,
        next_attempt_at: datetime | None = None,
    ) -> DeliveryRecord:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(DeliveryRow).where(DeliveryRow.id == delivery_id).with_for_update()
            )
            if row is None:
                raise KeyError(delivery_id)
            validate_delivery_transition(DeliveryStatus(row.status), status)
            effective_error = (
                DeliveryError.model_validate(row.last_error)
                if status == row.status and last_error is None and row.last_error
                else last_error
            )
            row.status = str(status)
            row.last_error = effective_error.model_dump(mode="json") if effective_error else None
            row.approved_at = approved_at or row.approved_at
            row.next_attempt_at = next_attempt_at
            row.updated_at = datetime.now(UTC)
            await session.flush()
            return delivery_record(row)

    async def claim_delivery(self, delivery_id: UUID) -> DeliveryRecord | None:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            row = await session.scalar(
                update(DeliveryRow)
                .where(
                    DeliveryRow.id == delivery_id,
                    DeliveryRow.status == DeliveryStatus.QUEUED,
                )
                .values(
                    status=str(DeliveryStatus.PROCESSING),
                    last_error=None,
                    next_attempt_at=None,
                    updated_at=now,
                )
                .returning(DeliveryRow)
            )
            return delivery_record(row) if row is not None else None

    async def record_delivery_result(self, result: DeliveryResult) -> DeliveryRecord:
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(DeliveryRow).where(DeliveryRow.id == result.delivery_id).with_for_update()
            )
            if row is None:
                raise KeyError(result.delivery_id)
            existing = await session.get(DeliveryAttemptRow, (result.delivery_id, result.attempt))
            if existing is not None:
                persisted = DeliveryResult.model_validate(existing.payload)
                if persisted != result:
                    raise ValueError("delivery attempt is immutable")
                return delivery_record(row)
            if row.status != DeliveryStatus.PROCESSING:
                raise ValueError("delivery results can only complete processing deliveries")
            if result.connector_id != row.connector_id:
                raise ValueError("delivery result connector does not match its request")
            if result.attempt != row.attempt_count + 1:
                raise ValueError("delivery attempts must be recorded sequentially")
            status = (
                DeliveryStatus.SUCCEEDED if result.status == "succeeded" else DeliveryStatus.FAILED
            )
            session.add(
                DeliveryAttemptRow(
                    delivery_id=result.delivery_id,
                    attempt=result.attempt,
                    status=str(status),
                    payload=result.model_dump(mode="json"),
                    recorded_at=result.recorded_at,
                )
            )
            row.status = str(status)
            row.attempt_count = result.attempt
            row.last_error = (
                result.error.model_dump(mode="json") if result.error is not None else None
            )
            row.updated_at = result.recorded_at
            await session.flush()
            return delivery_record(row)

    async def delivery_attempts(self, delivery_id: UUID) -> list[DeliveryResult]:
        async with self.sessions() as session:
            rows = list(
                (
                    await session.scalars(
                        select(DeliveryAttemptRow)
                        .where(DeliveryAttemptRow.delivery_id == delivery_id)
                        .order_by(DeliveryAttemptRow.attempt)
                    )
                ).all()
            )
        return [DeliveryResult.model_validate(row.payload) for row in rows]

    async def register_run(self, run: Run, tasks: list[AgentTask]) -> None:
        async with self.sessions.begin() as session:
            run_row = RunRow(
                id=run.id,
                target_id=run.request.target_id,
                trigger=run.request.trigger,
                status=str(run.status),
                seed=run.request.seed,
                commit_sha=run.request.commit_sha,
                request=run.request.model_dump(mode="json"),
                policy_version=run.policy_version,
                started_at=run.started_at,
                ended_at=run.ended_at,
                cancellation_requested=run.cancellation_requested,
            )
            session.add(run_row)
            await session.flush()
            session.add_all(task_row(task) for task in tasks)

    async def register_tasks(self, tasks: list[AgentTask]) -> None:
        if not tasks:
            return
        async with self.sessions.begin() as session:
            session.add_all(task_row(task) for task in tasks)

    async def update_task_status(self, task_id: UUID, status: TaskStatus) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(AgentTaskRow).where(AgentTaskRow.id == task_id).values(status=str(status))
            )

    async def update_run(self, run: Run) -> None:
        async with self.sessions.begin() as session:
            await session.execute(
                update(RunRow)
                .where(RunRow.id == run.id)
                .values(
                    status=str(run.status),
                    ended_at=run.ended_at,
                    cancellation_requested=run.cancellation_requested,
                )
            )

    async def load_state(self) -> PersistedState:
        async with self.sessions() as session:
            run_rows = list((await session.scalars(select(RunRow).order_by(RunRow.id))).all())
            task_rows = list(
                (await session.scalars(select(AgentTaskRow).order_by(AgentTaskRow.id))).all()
            )
            result_rows = list(
                (
                    await session.scalars(select(AgentResultRow).order_by(AgentResultRow.task_id))
                ).all()
            )
            anomaly_rows = list(
                (await session.scalars(select(AnomalyRow).order_by(AnomalyRow.id))).all()
            )
            defect_rows = list(
                (await session.scalars(select(DefectRow).order_by(DefectRow.id))).all()
            )
            gate_rows = list(
                (await session.scalars(select(GateRow).order_by(GateRow.run_id))).all()
            )
            report_rows = list(
                (await session.scalars(select(RunReportRow).order_by(RunReportRow.id))).all()
            )
            summary_rows = list(
                (
                    await session.scalars(select(ReleaseSummaryRow).order_by(ReleaseSummaryRow.id))
                ).all()
            )
            delivery_rows = list(
                (await session.scalars(select(DeliveryRow).order_by(DeliveryRow.id))).all()
            )
        return PersistedState(
            runs=tuple(
                Run(
                    id=row.id,
                    request=RunRequest.model_validate(row.request),
                    status=row.status,
                    policy_version=row.policy_version,
                    started_at=row.started_at,
                    ended_at=row.ended_at,
                    cancellation_requested=row.cancellation_requested,
                )
                for row in run_rows
            ),
            tasks=tuple(agent_task(row) for row in task_rows),
            results=tuple(AgentResult.model_validate(row.payload) for row in result_rows),
            anomalies=tuple(Anomaly.model_validate(row.payload) for row in anomaly_rows),
            defects=tuple(Defect.model_validate(row.payload) for row in defect_rows),
            gates=tuple(GateDecision.model_validate(row.payload) for row in gate_rows),
            reports=tuple(RunReport.model_validate(row.payload) for row in report_rows),
            summaries=tuple(ReleaseSummary.model_validate(row.payload) for row in summary_rows),
            deliveries=tuple(delivery_record(row) for row in delivery_rows),
        )

    async def recover_expired_tasks(self) -> tuple[UUID, ...]:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            rows = tuple(
                (
                    await session.scalars(
                        select(AgentTaskRow.id).where(
                            AgentTaskRow.status.in_([TaskStatus.LEASED, TaskStatus.RUNNING]),
                            AgentTaskRow.lease_expires_at <= now,
                        )
                    )
                ).all()
            )
            if rows:
                await session.execute(
                    update(AgentTaskRow)
                    .where(AgentTaskRow.id.in_(rows))
                    .values(
                        status=str(TaskStatus.READY),
                        lease_owner=None,
                        heartbeat_at=None,
                        lease_expires_at=None,
                    )
                )
        return rows

    async def lease_task(
        self,
        worker: str,
        agent_types: tuple[str, ...] = (),
        partition_prefixes: tuple[str, ...] = (),
    ) -> AgentTask | None:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            query = select(AgentTaskRow).where(
                AgentTaskRow.status == TaskStatus.READY,
                AgentTaskRow.deadline > now,
            )
            if agent_types:
                query = query.where(AgentTaskRow.agent_type.in_(agent_types))
            if partition_prefixes:
                query = query.where(
                    or_(
                        *(
                            AgentTaskRow.target_partition.startswith(prefix, autoescape=True)
                            for prefix in partition_prefixes
                        )
                    )
                )
            candidates = list(
                (
                    await session.scalars(
                        query.order_by(AgentTaskRow.deadline, AgentTaskRow.id).with_for_update(
                            skip_locked=True
                        )
                    )
                ).all()
            )
            if not candidates:
                return None
            run_ids = {row.run_id for row in candidates}
            status_rows = (
                await session.execute(
                    select(AgentTaskRow.id, AgentTaskRow.status).where(
                        AgentTaskRow.run_id.in_(run_ids)
                    )
                )
            ).all()
            statuses: dict[UUID, str] = {task_id: status for task_id, status in status_rows}
            active_partitions = set(
                (
                    await session.scalars(
                        select(AgentTaskRow.target_partition).where(
                            AgentTaskRow.run_id.in_(run_ids),
                            AgentTaskRow.status.in_([TaskStatus.LEASED, TaskStatus.RUNNING]),
                        )
                    )
                ).all()
            )
            selected = next(
                (
                    row
                    for row in candidates
                    if row.target_partition not in active_partitions
                    and all(
                        statuses.get(UUID(dependency)) == TaskStatus.SUCCEEDED
                        for dependency in row.dependency_task_ids
                    )
                ),
                None,
            )
            if selected is None:
                return None
            selected.status = str(TaskStatus.LEASED)
            selected.lease_owner = worker
            selected.heartbeat_at = now
            selected.lease_expires_at = now + timedelta(seconds=45)
            await session.flush()
            return agent_task(selected)

    async def put_observation(self, observation: Observation) -> None:
        async with self.sessions.begin() as session:
            row = await session.get(
                ProductStateRow, (observation.run_id, observation.state_fingerprint)
            )
            if row is None:
                session.add(
                    ProductStateRow(
                        run_id=observation.run_id,
                        fingerprint=observation.state_fingerprint,
                        payload=observation.model_dump(mode="json"),
                        visited_count=1,
                    )
                )
            else:
                row.visited_count += 1

    async def put_transition(self, run_id: UUID, transition: Transition) -> None:
        async with self.sessions.begin() as session:
            existing = await session.scalar(
                select(ProductTransitionRow.id).where(
                    ProductTransitionRow.run_id == run_id,
                    ProductTransitionRow.from_state_id == transition.from_state_id,
                    ProductTransitionRow.action_id == transition.action_id,
                    ProductTransitionRow.to_state_id == transition.to_state_id,
                )
            )
            if existing is None:
                session.add(
                    ProductTransitionRow(
                        run_id=run_id,
                        from_state_id=transition.from_state_id,
                        action_id=transition.action_id,
                        to_state_id=transition.to_state_id,
                        payload=transition.model_dump(mode="json"),
                    )
                )

    async def coverage(self, run_id: UUID) -> dict[str, Any]:
        async with self.sessions() as session:
            states = list(
                (
                    await session.scalars(
                        select(ProductStateRow).where(ProductStateRow.run_id == run_id)
                    )
                ).all()
            )
            transitions = list(
                (
                    await session.scalars(
                        select(ProductTransitionRow).where(ProductTransitionRow.run_id == run_id)
                    )
                ).all()
            )
        routes = {str(row.payload["page"]["route"]) for row in states}
        roles = {str(row.payload["persona"]["role"]) for row in states}
        return {
            "states": len(states),
            "transitions": len(transitions),
            "routes": len(routes),
            "roles": len(roles),
            "graph": {
                "nodes": [
                    {
                        "id": row.fingerprint,
                        "route": row.payload["page"]["route"],
                        "role": row.payload["persona"]["role"],
                        "visited_count": row.visited_count,
                    }
                    for row in states
                ],
                "edges": [row.payload for row in transitions],
            },
        }

    @staticmethod
    def _event_row(event: EventEnvelope) -> EventRow:
        return EventRow(
            id=event.event_id,
            run_id=event.run_id,
            event_type=event.event_type,
            occurred_at=event.occurred_at,
            correlation_id=event.correlation_id,
            producer=event.producer,
            payload=event.payload,
        )
