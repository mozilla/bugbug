"""Add notify_email to runs.

Revision ID: 7f3a9d21b6c4
Revises: b5b896e1ce12
Create Date: 2026-07-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f3a9d21b6c4"
down_revision: Union[str, Sequence[str], None] = "b5b896e1ce12"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("runs", sa.Column("notify_email", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("runs", "notify_email")
