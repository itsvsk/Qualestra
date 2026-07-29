"""Persist deterministic reports and provider-neutral delivery outbox state.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "run_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["qa_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "source_fingerprint", "kind", "format"),
    )
    for name in ("run_id", "kind", "digest", "source_fingerprint", "generated_at"):
        op.create_index(f"ix_run_reports_{name}", "run_reports", [name])

    op.create_table(
        "release_summaries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.String(length=20), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["run_reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["qa_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "source_fingerprint"),
    )
    for name in ("run_id", "report_id", "source_fingerprint", "generated_at"):
        op.create_index(f"ix_release_summaries_{name}", "release_summaries", [name])

    op.create_table(
        "deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.String(length=80), nullable=False),
        sa.Column("destination_id", sa.String(length=120), nullable=False),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=80), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.JSON(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["run_reports.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["qa_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    for name in (
        "run_id",
        "report_id",
        "connector_id",
        "status",
        "next_attempt_at",
        "created_at",
        "updated_at",
    ):
        op.create_index(f"ix_deliveries_{name}", "deliveries", [name])

    op.create_table(
        "delivery_attempts",
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["delivery_id"], ["deliveries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("delivery_id", "attempt"),
    )
    op.create_index("ix_delivery_attempts_status", "delivery_attempts", ["status"])
    op.create_index("ix_delivery_attempts_recorded_at", "delivery_attempts", ["recorded_at"])


def downgrade() -> None:
    op.drop_index("ix_delivery_attempts_recorded_at", table_name="delivery_attempts")
    op.drop_index("ix_delivery_attempts_status", table_name="delivery_attempts")
    op.drop_table("delivery_attempts")
    for name in (
        "updated_at",
        "created_at",
        "next_attempt_at",
        "status",
        "connector_id",
        "report_id",
        "run_id",
    ):
        op.drop_index(f"ix_deliveries_{name}", table_name="deliveries")
    op.drop_table("deliveries")
    for name in ("generated_at", "source_fingerprint", "report_id", "run_id"):
        op.drop_index(f"ix_release_summaries_{name}", table_name="release_summaries")
    op.drop_table("release_summaries")
    for name in ("generated_at", "source_fingerprint", "digest", "kind", "run_id"):
        op.drop_index(f"ix_run_reports_{name}", table_name="run_reports")
    op.drop_table("run_reports")
