"""Tests for Phabricator webhook author authorization."""

from unittest.mock import AsyncMock

from app.phabricator_authorization import PhabricatorAuthorizer


class _FakeClient:
    def __init__(self, members: frozenset[str]) -> None:
        self.get_project_members = AsyncMock(return_value=members)


async def test_is_authorized_uses_cached_member_list():
    authorizer = PhabricatorAuthorizer("PHID-PROJ-test")
    client = _FakeClient(frozenset({"PHID-USER-authorized"}))

    assert await authorizer.is_authorized(client, "PHID-USER-authorized") is True
    assert await authorizer.is_authorized(client, "PHID-USER-authorized") is True
    client.get_project_members.assert_awaited_once_with("PHID-PROJ-test")


async def test_is_authorized_refreshes_once_for_unknown_authors():
    authorizer = PhabricatorAuthorizer("PHID-PROJ-test")
    client = _FakeClient(frozenset({"PHID-USER-authorized"}))

    assert await authorizer.is_authorized(client, "PHID-USER-unknown-one") is False
    assert await authorizer.is_authorized(client, "PHID-USER-unknown-two") is False
    client.get_project_members.assert_awaited_once_with("PHID-PROJ-test")
