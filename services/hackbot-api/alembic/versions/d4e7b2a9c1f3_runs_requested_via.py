"""Record which service requested each run.

Revision ID: d4e7b2a9c1f3
Revises: 78015b96b6db
Create Date: 2026-11-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e7b2a9c1f3"
down_revision: Union[str, Sequence[str], None] = "78015b96b6db"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("runs", sa.Column("requested_via", sa.String(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("runs", "requested_via")
