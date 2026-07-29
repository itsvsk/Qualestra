from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sentinelqa.domain.enums import TaskStatus
from sentinelqa.domain.models import AgentResult, AgentTask

from .database import AgentResultRow, AgentTaskRow


class PostgresTaskRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    async def enqueue(self, task: AgentTask) -> None:
        async with self.sessions.begin() as session:
            session.add(
                AgentTaskRow(
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
            )

    async def lease_ready(self, worker: str, limit: int = 1, lease_seconds: int = 45) -> list[UUID]:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            statement = (
                select(AgentTaskRow)
                .where(and_(AgentTaskRow.status == TaskStatus.READY, AgentTaskRow.deadline > now))
                .order_by(AgentTaskRow.deadline, AgentTaskRow.id)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
            rows = list((await session.scalars(statement)).all())
            for row in rows:
                row.status = TaskStatus.LEASED
                row.lease_owner = worker
                row.heartbeat_at = now
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return [row.id for row in rows]

    async def recover_expired(self) -> int:
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            statement = (
                update(AgentTaskRow)
                .where(
                    AgentTaskRow.status.in_([TaskStatus.LEASED, TaskStatus.RUNNING]),
                    AgentTaskRow.lease_expires_at <= now,
                )
                .values(
                    status=TaskStatus.READY,
                    lease_owner=None,
                    heartbeat_at=None,
                    lease_expires_at=None,
                )
            )
            result = cast(CursorResult[Any], await session.execute(statement))
            return int(result.rowcount or 0)

    async def commit_result(self, result: AgentResult) -> None:
        async with self.sessions.begin() as session:
            session.add(
                AgentResultRow(
                    task_id=result.task_id,
                    schema_version=result.schema_version,
                    outcome=result.outcome,
                    result_ref=result.result_ref,
                    payload=result.model_dump(mode="json"),
                    produced_at=result.produced_at,
                )
            )
            status = TaskStatus.SUCCEEDED if result.outcome == "succeeded" else TaskStatus.FAILED
            await session.execute(
                update(AgentTaskRow).where(AgentTaskRow.id == result.task_id).values(status=status)
            )
