"""Add general_comment column.

Revision ID: b3e1c7d2a4f9
Revises: 714c920ab85b
Create Date: 2026-10-02 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e1c7d2a4f9"
down_revision: Union[str, Sequence[str], None] = "714c920ab85b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "review_requests",
        sa.Column("general_comment", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("review_requests", "general_comment")
