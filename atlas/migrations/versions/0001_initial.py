"""Initial Qualestra blackboard tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "qa_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=False),
        sa.Column("trigger", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("commit_sha", sa.String(length=80), nullable=True),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_qa_runs_status", "qa_runs", ["status"])
    op.create_index("ix_qa_runs_target_id", "qa_runs", ["target_id"])
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("parent_task_id", sa.Uuid(), nullable=True),
        sa.Column("agent_type", sa.String(length=60), nullable=False),
        sa.Column("agent_version", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("dependency_task_ids", sa.JSON(), nullable=False),
        sa.Column("input_ref", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("target_partition", sa.String(length=240), nullable=False),
        sa.Column("budget", sa.JSON(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=120), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["parent_task_id"], ["agent_tasks.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["qa_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "idempotency_key"),
    )
    for name in (
        "agent_type",
        "target_partition",
        "status",
        "lease_expires_at",
        "run_id",
        "lease_owner",
    ):
        op.create_index(f"ix_agent_tasks_{name}", "agent_tasks", [name])
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("producer", sa.String(length=120), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["qa_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for name in ("correlation_id", "occurred_at", "run_id", "event_type"):
        op.create_index(f"ix_events_{name}", "events", [name])
    op.create_table(
        "agent_results",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=40), nullable=False),
        sa.Column("result_ref", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("produced_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("task_id"),
    )


def downgrade() -> None:
    op.drop_table("agent_results")
    op.drop_table("events")
    op.drop_table("agent_tasks")
    op.drop_table("qa_runs")
