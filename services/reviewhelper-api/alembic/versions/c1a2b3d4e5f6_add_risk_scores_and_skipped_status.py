"""Add risk/complexity scores, test signals, and SKIPPED status.

Revision ID: c1a2b3d4e5f6
Revises: 714c920ab85b
Create Date: 2026-05-28 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from alembic_postgresql_enum import TableReference

# revision identifiers, used by Alembic.
revision: str = "c1a2b3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "714c920ab85b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.sync_enum_values(
        enum_schema="public",
        enum_name="review_status_enum",
        new_values=[
            "PENDING",
            "PROCESSING",
            "RETRY_PENDING",
            "PUBLISHED",
            "FAILED",
            "SKIPPED",
        ],
        affected_columns=[
            TableReference(
                table_schema="public",
                table_name="review_requests",
                column_name="status",
            )
        ],
        enum_values_to_rename=[],
    )

    op.add_column("review_requests", sa.Column("risk", sa.Integer(), nullable=True))
    op.add_column(
        "review_requests", sa.Column("complexity", sa.Integer(), nullable=True)
    )
    op.add_column(
        "review_requests", sa.Column("in_diff_test_signal", sa.Text(), nullable=True)
    )
    op.add_column(
        "review_requests", sa.Column("coverage_signal", sa.Text(), nullable=True)
    )
    op.add_column(
        "review_requests", sa.Column("skipped_reason", sa.Text(), nullable=True)
    )
    op.create_index(
        "ix_review_requests_status", "review_requests", ["status"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_review_requests_status", table_name="review_requests")
    op.drop_column("review_requests", "skipped_reason")
    op.drop_column("review_requests", "coverage_signal")
    op.drop_column("review_requests", "in_diff_test_signal")
    op.drop_column("review_requests", "complexity")
    op.drop_column("review_requests", "risk")

    op.sync_enum_values(
        enum_schema="public",
        enum_name="review_status_enum",
        new_values=[
            "PENDING",
            "PROCESSING",
            "RETRY_PENDING",
            "PUBLISHED",
            "FAILED",
        ],
        affected_columns=[
            TableReference(
                table_schema="public",
                table_name="review_requests",
                column_name="status",
            )
        ],
        enum_values_to_rename=[],
    )
