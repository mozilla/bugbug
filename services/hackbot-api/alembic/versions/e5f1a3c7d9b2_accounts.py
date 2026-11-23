"""Record who triggered each run, as an account on an identity provider.

Revision ID: e5f1a3c7d9b2
Revises: d4e7b2a9c1f3
Create Date: 2026-11-23 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f1a3c7d9b2"
down_revision: Union[str, Sequence[str], None] = "d4e7b2a9c1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("identity_provider", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "identity_provider",
            "external_id",
            name="uq_accounts_identity_provider_external_id",
        ),
    )
    op.add_column("runs", sa.Column("source", sa.String(), nullable=True))
    op.add_column("runs", sa.Column("account_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_runs_account_id_accounts", "runs", "accounts", ["account_id"], ["id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("fk_runs_account_id_accounts", "runs", type_="foreignkey")
    op.drop_column("runs", "account_id")
    op.drop_column("runs", "source")
    op.drop_table("accounts")
