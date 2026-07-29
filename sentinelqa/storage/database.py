from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "qa_runs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    target_id: Mapped[str] = mapped_column(String(120), index=True)
    trigger: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), index=True)
    seed: Mapped[int] = mapped_column(Integer)
    commit_sha: Mapped[str | None] = mapped_column(String(80))
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    policy_version: Mapped[str] = mapped_column(String(40))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, default=False)


class AgentTaskRow(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (UniqueConstraint("run_id", "idempotency_key"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("qa_runs.id", ondelete="CASCADE"), index=True)
    parent_task_id: Mapped[UUID | None] = mapped_column(ForeignKey("agent_tasks.id"))
    agent_type: Mapped[str] = mapped_column(String(60), index=True)
    agent_version: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(40), index=True)
    dependency_task_ids: Mapped[list[str]] = mapped_column(JSON)
    input_ref: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(80))
    target_partition: Mapped[str] = mapped_column(String(240), index=True)
    budget: Mapped[dict[str, Any]] = mapped_column(JSON)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer)
    lease_owner: Mapped[str | None] = mapped_column(String(120), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AgentResultRow(Base):
    __tablename__ = "agent_results"
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    schema_version: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str] = mapped_column(String(40))
    result_ref: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    produced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EventRow(Base):
    __tablename__ = "events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("qa_runs.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(120), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    correlation_id: Mapped[UUID] = mapped_column(index=True)
    producer: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ContextRow(Base):
    __tablename__ = "contexts"
    ref: Mapped[str] = mapped_column(String(240), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class AnomalyRow(Base):
    __tablename__ = "anomalies"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("qa_runs.id", ondelete="CASCADE"), index=True)
    signature: Mapped[str] = mapped_column(String(500), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class DefectRow(Base):
    __tablename__ = "defects"
    __table_args__ = (UniqueConstraint("run_id", "signature"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("qa_runs.id", ondelete="CASCADE"), index=True)
    signature: Mapped[str] = mapped_column(String(500), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class GateRow(Base):
    __tablename__ = "gate_decisions"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("qa_runs.id", ondelete="CASCADE"), primary_key=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class RunReportRow(Base):
    __tablename__ = "run_reports"
    __table_args__ = (UniqueConstraint("run_id", "source_fingerprint", "kind", "format"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("qa_runs.id", ondelete="CASCADE"), index=True)
    schema_version: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(40), index=True)
    format: Mapped[str] = mapped_column(String(20))
    digest: Mapped[str] = mapped_column(String(80), index=True)
    source_fingerprint: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ReleaseSummaryRow(Base):
    __tablename__ = "release_summaries"
    __table_args__ = (UniqueConstraint("run_id", "source_fingerprint"),)
    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("qa_runs.id", ondelete="CASCADE"), index=True)
    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("run_reports.id", ondelete="CASCADE"), index=True
    )
    schema_version: Mapped[str] = mapped_column(String(20))
    source_fingerprint: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DeliveryRow(Base):
    __tablename__ = "deliveries"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("qa_runs.id", ondelete="CASCADE"), index=True)
    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("run_reports.id", ondelete="CASCADE"), index=True
    )
    connector_id: Mapped[str] = mapped_column(String(80), index=True)
    destination_id: Mapped[str] = mapped_column(String(120))
    mode: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(40), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DeliveryAttemptRow(Base):
    __tablename__ = "delivery_attempts"
    delivery_id: Mapped[UUID] = mapped_column(
        ForeignKey("deliveries.id", ondelete="CASCADE"), primary_key=True
    )
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(40), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ProductStateRow(Base):
    __tablename__ = "product_states"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("qa_runs.id", ondelete="CASCADE"), primary_key=True
    )
    fingerprint: Mapped[str] = mapped_column(String(500), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    visited_count: Mapped[int] = mapped_column(Integer, default=1)


class ProductTransitionRow(Base):
    __tablename__ = "product_transitions"
    __table_args__ = (UniqueConstraint("run_id", "from_state_id", "action_id", "to_state_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("qa_runs.id", ondelete="CASCADE"), index=True)
    from_state_id: Mapped[str] = mapped_column(String(500), index=True)
    action_id: Mapped[UUID] = mapped_column(index=True)
    to_state_id: Mapped[str] = mapped_column(String(500), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True)
        self.sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def ready(self) -> None:
        async with self.engine.connect() as connection:
            await connection.run_sync(lambda sync_connection: sync_connection.dialect.name)

    async def close(self) -> None:
        await self.engine.dispose()
