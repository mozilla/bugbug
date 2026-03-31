"""Add reviewer_status column.

Revision ID: 30a5eeb5f400
Revises: 53f024d8e601
Create Date: 2026-02-19 16:26:32.258055

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "30a5eeb5f400"
down_revision: Union[str, Sequence[str], None] = "53f024d8e601"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

reviewer_status_enum = sa.Enum(
    "BLOCKING",
    "ADDED",
    "ACCEPTED",
    "REJECTED",
    "COMMENTED",
    "ACCEPTED_OLDER",
    "REJECTED_OLDER",
    "RESIGNED",
    name="reviewer_status_enum",
)


def upgrade() -> None:
    """Upgrade schema."""
    reviewer_status_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "review_requests",
        sa.Column("reviewer_status", reviewer_status_enum, nullable=True),
    )
    op.add_column(
        "feedback",
        sa.Column("reviewer_status", reviewer_status_enum, nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("feedback", "reviewer_status")
    op.drop_column("review_requests", "reviewer_status")
    reviewer_status_enum.drop(op.get_bind(), checkfirst=True)
