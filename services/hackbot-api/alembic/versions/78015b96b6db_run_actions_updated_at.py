"""Track when an action last changed, so a crashed apply pass can be recovered.

Revision ID: 78015b96b6db
Revises: a7d4e9c21b83
Create Date: 2026-10-09 18:33:49.883457

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "78015b96b6db"
down_revision: Union[str, Sequence[str], None] = "a7d4e9c21b83"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "run_actions",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE run_actions SET updated_at = COALESCE(applied_at, created_at)")
    op.alter_column(
        "run_actions",
        "updated_at",
        nullable=False,
        server_default=sa.text("now()"),
        existing_type=sa.DateTime(timezone=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("run_actions", "updated_at")
