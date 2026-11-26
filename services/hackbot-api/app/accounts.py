"""Accounts: the people who trigger runs, one row per person per identity provider."""

from sqlalchemy import and_, case, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Account
from app.schemas import IdentityProvider


async def find_or_create_account(
    db: AsyncSession,
    identity_provider: IdentityProvider,
    external_id: str,
    username: str | None,
) -> int:
    """Return the ID of this person's account, creating it on first sight.

    One statement, so two requests for the same new person cannot both insert.
    A username the provider reports is kept up to date; a missing one leaves
    the stored username alone.
    """
    insert = pg_insert(Account).values(
        identity_provider=identity_provider,
        external_id=external_id,
        username=username,
    )
    renamed = and_(
        insert.excluded.username.is_not(None),
        insert.excluded.username.is_distinct_from(Account.username),
    )
    stmt = insert.on_conflict_do_update(
        index_elements=["identity_provider", "external_id"],
        set_={
            "username": func.coalesce(insert.excluded.username, Account.username),
            "updated_at": case((renamed, func.now()), else_=Account.updated_at),
        },
    ).returning(Account.id)
    return await db.scalar(stmt)
