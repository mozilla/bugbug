"""Run actions and finalized_at.

Revision ID: c1a2f3e4b5d6
Revises: b5b896e1ce12
Create Date: 2026-07-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c1a2f3e4b5d6"
down_revision: Union[str, Sequence[str], None] = "b5b896e1ce12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "runs", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "run_actions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ref", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "idx", name="uq_run_actions_run_idx"),
    )
    op.create_index(
        op.f("ix_run_actions_run_id"), "run_actions", ["run_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_run_actions_run_id"), table_name="run_actions")
    op.drop_table("run_actions")
    op.drop_column("runs", "finalized_at")
