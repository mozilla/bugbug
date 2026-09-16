"""Name the work a run does, so duplicate triggers can collapse onto it.

Revision ID: a7d4e9c21b83
Revises: f3c8a1d5b2e7
Create Date: 2026-09-08 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d4e9c21b83"
down_revision: Union[str, Sequence[str], None] = "f3c8a1d5b2e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("runs", sa.Column("dedupe_key", sa.String(), nullable=True))
    # Unique, because a key names one run for good: the constraint is what
    # decides a duplicate trigger, not just a check on one. `dedupe_key` leads
    # so the index also serves the `?dedupe_key=` listing filter, which carries
    # no agent. Existing rows all have a NULL key, and NULLs do not collide, so
    # this is safe to add to a populated table.
    op.create_index(
        "uq_runs_dedupe_key",
        "runs",
        ["dedupe_key", "agent"],
        unique=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_runs_dedupe_key", table_name="runs")
    op.drop_column("runs", "dedupe_key")
