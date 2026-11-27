"""Accounts: the people who trigger runs, one row per person per identity provider."""

from sqlalchemy import select
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
    """Return the ID of this person's account, creating it the first time.

    A username the provider reports is kept up to date; a missing one leaves
    the stored username alone.
    """
    same_person = select(Account).where(
        Account.identity_provider == identity_provider,
        Account.external_id == external_id,
    )
    account = await db.scalar(same_person)
    if account is not None:
        if username is not None and username != account.username:
            account.username = username
        return account.id

    # Only two requests for the same new person at once can conflict here; the
    # other one has then just created the account, so it is read back.
    account_id = await db.scalar(
        pg_insert(Account)
        .values(
            identity_provider=identity_provider,
            external_id=external_id,
            username=username,
        )
        .on_conflict_do_nothing(index_elements=["identity_provider", "external_id"])
        .returning(Account.id)
    )
    if account_id is None:
        account_id = (await db.scalar(same_person)).id
    return account_id
