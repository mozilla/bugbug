"""Persist CI continuations independently of an agent execution.

Revision ID: c4e91a082b7f
Revises: a7d4e9c21b83
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4e91a082b7f"
down_revision = "a7d4e9c21b83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("ci_wait", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "ci_wait")
