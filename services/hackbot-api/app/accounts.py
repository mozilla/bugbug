"""Accounts: the people who trigger runs, one row per person per identity provider."""

from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends, Header, HTTPException
from pydantic import BeforeValidator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.connection import get_db
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


async def get_account_id(
    db: Annotated[AsyncSession, Depends(get_db)],
    identity_provider: Annotated[
        IdentityProvider | None,
        Header(
            alias="Hackbot-Requester-Identity-Provider",
            description=(
                "The service `Hackbot-Requester-Id` belongs to, e.g. `bugzilla`."
            ),
        ),
    ] = None,
    user_id: Annotated[
        str | None,
        Header(
            alias="Hackbot-Requester-Id",
            description="The stable ID, on the identity provider, of the person "
            "who triggered the request.",
        ),
    ] = None,
    username: Annotated[
        str | None,
        # Sent percent-encoded, as headers are ASCII-only.
        BeforeValidator(lambda value: unquote(value) or None),
        Header(
            alias="Hackbot-Requester-Username",
            description=(
                "That person's readable name on the identity provider, as "
                "percent-encoded UTF-8."
            ),
        ),
    ] = None,
) -> int | None:
    """Dependency: the account of the person a request names, if it names one.

    It shares the request's session, so the account is saved with whatever the
    endpoint commits.
    """
    if not identity_provider and not user_id:
        return None
    if not (identity_provider and user_id):
        raise HTTPException(
            status_code=422,
            detail=(
                "Hackbot-Requester-Identity-Provider and Hackbot-Requester-Id "
                "must be sent together"
            ),
        )
    return await find_or_create_account(db, identity_provider, user_id, username)
