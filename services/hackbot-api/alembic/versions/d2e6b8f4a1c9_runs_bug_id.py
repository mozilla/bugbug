"""Store the bug a run is for, so runs can be listed by bug id.

Revision ID: d2e6b8f4a1c9
Revises: d4e7b2a9c1f3
Create Date: 2026-11-10 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e6b8f4a1c9"
down_revision: Union[str, Sequence[str], None] = "d4e7b2a9c1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("runs", sa.Column("bug_id", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE runs SET bug_id = (inputs ->> 'bug_id')::integer "
        "WHERE jsonb_typeof(inputs -> 'bug_id') = 'number'"
    )
    op.create_index(op.f("ix_runs_bug_id"), "runs", ["bug_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_runs_bug_id"), table_name="runs")
    op.drop_column("runs", "bug_id")
