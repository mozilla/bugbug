"""Run feedback.

Revision ID: d7e2f9a4c1b3
Revises: f3c8a1d5b2e7
Create Date: 2026-08-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d7e2f9a4c1b3"
down_revision: Union[str, Sequence[str], None] = "f3c8a1d5b2e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "run_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("rating", sa.String(), nullable=False),
        sa.Column(
            "dimensions", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("rater_kind", sa.String(), nullable=False),
        sa.Column("anon_id", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_run_feedback_run_id"), "run_feedback", ["run_id"], unique=False
    )
    # Partial, because a request carrying neither an IP nor a user agent has no
    # dedupe key and those rows must not collide with each other.
    op.create_index(
        "uq_run_feedback_anon",
        "run_feedback",
        ["run_id", "anon_id"],
        unique=True,
        postgresql_where=sa.text("anon_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_run_feedback_anon", table_name="run_feedback")
    op.drop_index(op.f("ix_run_feedback_run_id"), table_name="run_feedback")
    op.drop_table("run_feedback")
