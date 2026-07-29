"""Persist product states and transitions.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_states",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=500), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("visited_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["qa_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id", "fingerprint"),
    )
    op.create_table(
        "product_transitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("from_state_id", sa.String(length=500), nullable=False),
        sa.Column("action_id", sa.Uuid(), nullable=False),
        sa.Column("to_state_id", sa.String(length=500), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["qa_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "from_state_id", "action_id", "to_state_id"),
    )
    op.create_index("ix_product_transitions_run_id", "product_transitions", ["run_id"])
    op.create_index(
        "ix_product_transitions_from_state_id", "product_transitions", ["from_state_id"]
    )
    op.create_index("ix_product_transitions_action_id", "product_transitions", ["action_id"])
    op.create_index("ix_product_transitions_to_state_id", "product_transitions", ["to_state_id"])


def downgrade() -> None:
    op.drop_index("ix_product_transitions_to_state_id", table_name="product_transitions")
    op.drop_index("ix_product_transitions_action_id", table_name="product_transitions")
    op.drop_index("ix_product_transitions_from_state_id", table_name="product_transitions")
    op.drop_index("ix_product_transitions_run_id", table_name="product_transitions")
    op.drop_table("product_transitions")
    op.drop_table("product_states")
