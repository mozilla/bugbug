"""Add explanation, order, severity, category and confidence to generated comments.

Revision ID: c4d8e2f1a7b3
Revises: b3e1c7d2a4f9
Create Date: 2026-10-17 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d8e2f1a7b3"
down_revision: Union[str, Sequence[str], None] = "b3e1c7d2a4f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

comment_severity_enum = sa.Enum(
    "CRITICAL",
    "MAJOR",
    "MINOR",
    "NIT",
    name="comment_severity_enum",
)

comment_category_enum = sa.Enum(
    "SECURITY",
    "CORRECTNESS",
    "PERFORMANCE",
    "MAINTAINABILITY",
    "STYLE",
    "SCOPE",
    name="comment_category_enum",
)

comment_confidence_enum = sa.Enum(
    "HIGH",
    "MEDIUM",
    name="comment_confidence_enum",
)


def upgrade() -> None:
    """Upgrade schema."""
    for enum_type in (
        comment_severity_enum,
        comment_category_enum,
        comment_confidence_enum,
    ):
        enum_type.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "generated_comments",
        sa.Column("explanation", sa.Text(), nullable=True),
    )
    op.add_column(
        "generated_comments",
        sa.Column("order", sa.Integer(), nullable=True),
    )
    op.add_column(
        "generated_comments",
        sa.Column("severity", comment_severity_enum, nullable=True),
    )
    op.add_column(
        "generated_comments",
        sa.Column("category", comment_category_enum, nullable=True),
    )
    op.add_column(
        "generated_comments",
        sa.Column("confidence", comment_confidence_enum, nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("generated_comments", "confidence")
    op.drop_column("generated_comments", "category")
    op.drop_column("generated_comments", "severity")
    op.drop_column("generated_comments", "order")
    op.drop_column("generated_comments", "explanation")

    for enum_type in (
        comment_confidence_enum,
        comment_category_enum,
        comment_severity_enum,
    ):
        enum_type.drop(op.get_bind(), checkfirst=True)
