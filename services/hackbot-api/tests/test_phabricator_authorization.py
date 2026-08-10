"""Tests for Phabricator webhook author authorization."""

from unittest.mock import AsyncMock

from app.phabricator_authorization import PhabricatorAuthorizer


class _FakeClient:
    def __init__(self, members: frozenset[str]) -> None:
        self.get_project_members = AsyncMock(return_value=members)


async def test_is_authorized_uses_cached_member_list():
    client = _FakeClient(frozenset({"PHID-USER-authorized"}))
    authorizer = PhabricatorAuthorizer(client, "PHID-PROJ-test")

    assert await authorizer.is_authorized("PHID-USER-authorized") is True
    assert await authorizer.is_authorized("PHID-USER-authorized") is True
    client.get_project_members.assert_awaited_once_with("PHID-PROJ-test")


async def test_is_authorized_refreshes_once_for_unknown_authors():
    client = _FakeClient(frozenset({"PHID-USER-authorized"}))
    authorizer = PhabricatorAuthorizer(client, "PHID-PROJ-test")

    assert await authorizer.is_authorized("PHID-USER-unknown-one") is False
    assert await authorizer.is_authorized("PHID-USER-unknown-two") is False
    client.get_project_members.assert_awaited_once_with("PHID-PROJ-test")
