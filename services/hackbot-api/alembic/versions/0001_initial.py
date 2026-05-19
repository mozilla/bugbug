"""initial schema: runs table.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-16

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001_initial"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("agent", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("inputs", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("execution_name", sa.String(), nullable=True),
        sa.Column("results_prefix", sa.String(), nullable=False),
        sa.Column("summary", JSONB(), nullable=True),
        sa.Column(
            "artifacts",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_runs_agent", "runs", ["agent"])
    op.create_index("ix_runs_status", "runs", ["status"])
    op.create_index("ix_runs_created_at", "runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_runs_created_at", table_name="runs")
    op.drop_index("ix_runs_status", table_name="runs")
    op.drop_index("ix_runs_agent", table_name="runs")
    op.drop_table("runs")
