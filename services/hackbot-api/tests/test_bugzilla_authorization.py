"""Tests for Bugzilla webhook actor authorization."""

from unittest.mock import AsyncMock

from app.bugzilla_authorization import AUTHORIZED_GROUP_ID, BugzillaAuthorizer


class _FakeClient:
    def __init__(self, member: bool) -> None:
        self.is_user_in_group = AsyncMock(return_value=member)


def _authorizer(member: bool) -> tuple[BugzillaAuthorizer, _FakeClient]:
    client = _FakeClient(member)
    return BugzillaAuthorizer(client, AUTHORIZED_GROUP_ID), client


async def test_is_authorized_caches_positive_lookup():
    authorizer, client = _authorizer(member=True)

    assert await authorizer.is_authorized("dev@mozilla.com") is True
    assert await authorizer.is_authorized("dev@mozilla.com") is True
    client.is_user_in_group.assert_awaited_once_with(
        "dev@mozilla.com", AUTHORIZED_GROUP_ID
    )


async def test_is_authorized_caches_negative_lookup():
    authorizer, client = _authorizer(member=False)

    assert await authorizer.is_authorized("someone@example.com") is False
    assert await authorizer.is_authorized("someone@example.com") is False
    client.is_user_in_group.assert_awaited_once_with(
        "someone@example.com", AUTHORIZED_GROUP_ID
    )


async def test_is_authorized_normalizes_login_case():
    authorizer, client = _authorizer(member=True)

    assert await authorizer.is_authorized("Dev@Mozilla.com") is True
    assert await authorizer.is_authorized("dev@mozilla.com") is True
    client.is_user_in_group.assert_awaited_once_with(
        "dev@mozilla.com", AUTHORIZED_GROUP_ID
    )
