"""Record who requested each run.

Revision ID: f3c8a1d5b2e7
Revises: c1a2f3e4b5d6
Create Date: 2026-07-27 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3c8a1d5b2e7"
down_revision: Union[str, Sequence[str], None] = "c1a2f3e4b5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("runs", sa.Column("requested_by", sa.String(), nullable=True))
    op.create_index(
        op.f("ix_runs_requested_by"), "runs", ["requested_by"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_runs_requested_by"), table_name="runs")
    op.drop_column("runs", "requested_by")
