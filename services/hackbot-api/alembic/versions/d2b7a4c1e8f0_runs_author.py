"""Record the author (triggering user) on runs.

Revision ID: d2b7a4c1e8f0
Revises: c1a2f3e4b5d6
Create Date: 2026-07-27 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2b7a4c1e8f0"
down_revision: Union[str, Sequence[str], None] = "c1a2f3e4b5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("runs", sa.Column("author", sa.String(), nullable=True))
    op.create_index(op.f("ix_runs_author"), "runs", ["author"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_runs_author"), table_name="runs")
    op.drop_column("runs", "author")
