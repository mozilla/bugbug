# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import asyncio
import copy
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from unittest.mock import patch as mock_patch

import httpx
import pytest
from libmozdata.phabricator import ConduitError

from bugbug import phabricator
from bugbug.tools.core.platforms import phabricator as phab_platform


def test_stack_patches_start_at_bottom_ancestor() -> None:
    class FakePatch(phab_platform.PhabricatorPatch):
        def __init__(self, revision_phid="PHID-TOP"):
            self._revision_phid = revision_phid

        async def load(self):
            pass

        @property
        def _revision_metadata(self):
            return {"phid": self._revision_phid}

        @property
        def stack_graph(self):
            return {
                "PHID-BASE": [],
                "PHID-MIDDLE": ["PHID-BASE"],
                "PHID-TOP": ["PHID-MIDDLE"],
            }

    stack = asyncio.run(FakePatch().get_stack_patches())
    assert [patch.revision_phid for patch in stack] == [
        "PHID-BASE",
        "PHID-MIDDLE",
        "PHID-TOP",
    ]


def test_get_first_review_time() -> None:
    # No transactions.
    transactions: list[phabricator.TransactionDict] = []
    assert (
        phabricator.get_first_review_time(
            phabricator.RevisionDict({"id": 1, "transactions": transactions})
        )
        is None
    )

    # Revision accepted after 9 days.
    transactions = [
        phabricator.TransactionDict(
            {
                "type": "create",
                "dateCreated": 671760000,  # 16 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "accept",
                "dateCreated": 672537600,  # 25 April 1991
            }
        ),
    ]
    assert phabricator.get_first_review_time(
        phabricator.RevisionDict({"id": 1, "transactions": transactions})
    ) == timedelta(days=9)

    # Revision rejected after 9 days.
    transactions = [
        phabricator.TransactionDict(
            {
                "type": "create",
                "dateCreated": 671760000,  # 16 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "request-changes",
                "dateCreated": 672537600,  # 25 April 1991
            }
        ),
    ]
    assert phabricator.get_first_review_time(
        phabricator.RevisionDict({"id": 1, "transactions": transactions})
    ) == timedelta(days=9)

    # Changes planned after the revision was accepted in 9 days.
    transactions = [
        phabricator.TransactionDict(
            {
                "type": "create",
                "dateCreated": 671760000,  # 16 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "accept",
                "dateCreated": 672537600,  # 25 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "plan-changes",
                "dateCreated": 672883200,  # 29 April 1991
            }
        ),
    ]
    assert phabricator.get_first_review_time(
        phabricator.RevisionDict({"id": 1, "transactions": transactions})
    ) == timedelta(days=9)

    # Changes planned before the revision was accepted in 13 days.
    transactions = [
        phabricator.TransactionDict(
            {
                "type": "create",
                "dateCreated": 671760000,  # 16 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "plan-changes",
                "dateCreated": 672537600,  # 25 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "accept",
                "dateCreated": 672883200,  # 29 April 1991
            }
        ),
    ]
    assert phabricator.get_first_review_time(
        phabricator.RevisionDict({"id": 1, "transactions": transactions})
    ) == timedelta(days=13)

    # Changes planned and updated before the revision was accepted in 13 days.
    transactions = [
        phabricator.TransactionDict(
            {
                "type": "create",
                "dateCreated": 671760000,  # 16 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "plan-changes",
                "dateCreated": 672537600,  # 25 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "update",
                "dateCreated": 672624000,  # 26 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "accept",
                "dateCreated": 672883200,  # 29 April 1991
            }
        ),
    ]
    assert phabricator.get_first_review_time(
        phabricator.RevisionDict({"id": 1, "transactions": transactions})
    ) == timedelta(days=12)

    # Changes planned before the revision was accepted in 10 days, and updated after.
    transactions = [
        phabricator.TransactionDict(
            {
                "type": "create",
                "dateCreated": 671760000,  # 16 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "plan-changes",
                "dateCreated": 672537600,  # 25 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "request-changes",
                "dateCreated": 672624000,  # 26 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "update",
                "dateCreated": 672883200,  # 29 April 1991
            }
        ),
    ]
    assert phabricator.get_first_review_time(
        phabricator.RevisionDict({"id": 1, "transactions": transactions})
    ) == timedelta(days=10)

    # Changes planned, closed and reopened before the revision was accepted in 13 days.
    transactions = [
        phabricator.TransactionDict(
            {
                "type": "create",
                "dateCreated": 671760000,  # 16 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "plan-changes",
                "dateCreated": 672537600,  # 25 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "close",
                "dateCreated": 672624000,  # 26 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "reopen",
                "dateCreated": 672710400,  # 27 April 1991
            }
        ),
        phabricator.TransactionDict(
            {
                "type": "accept",
                "dateCreated": 672883200,  # 29 April 1991
            }
        ),
    ]
    assert phabricator.get_first_review_time(
        phabricator.RevisionDict({"id": 1, "transactions": transactions})
    ) == timedelta(days=11)


# ---------------------------------------------------------------------------
# Conduit through the shared HTTP client
# ---------------------------------------------------------------------------


def _conduit_client(*responses) -> MagicMock:
    request = httpx.Request("POST", "https://phabricator.example/api/")
    client = MagicMock()
    client.post = AsyncMock(
        side_effect=[
            httpx.Response(status, json=body, request=request)
            for status, body in responses
        ]
    )
    return client


@pytest.mark.asyncio
async def test_conduit_posts_through_shared_client(monkeypatch) -> None:
    monkeypatch.setenv("PHABRICATOR_API_KEY", "api-key")
    client = _conduit_client((200, {"result": {"data": []}, "error_code": None}))
    monkeypatch.setattr(phab_platform, "get_http_client", lambda: client)

    result = await phab_platform._conduit("user.search", constraints={"ids": [1]})

    assert result == {"data": []}
    (url,), kwargs = client.post.await_args
    assert url == f"{phab_platform._base_url()}/api/user.search"
    assert kwargs["data"]["output"] == "json"
    assert json.loads(kwargs["data"]["params"]) == {
        "constraints": {"ids": [1]},
        "__conduit__": {"token": "api-key"},
    }


@pytest.mark.asyncio
async def test_conduit_retries_server_errors(monkeypatch) -> None:
    client = _conduit_client((502, {}), (200, {"result": "ok", "error_code": None}))
    monkeypatch.setattr(phab_platform, "get_http_client", lambda: client)
    monkeypatch.setattr(phab_platform._conduit.retry, "sleep", AsyncMock())

    assert await phab_platform._conduit("user.search") == "ok"
    assert client.post.await_count == 2


@pytest.mark.asyncio
async def test_conduit_does_not_retry_conduit_errors(monkeypatch) -> None:
    client = _conduit_client(
        (200, {"result": None, "error_code": "ERR-CONDUIT-CORE", "error_info": "x"})
    )
    monkeypatch.setattr(phab_platform, "get_http_client", lambda: client)

    with pytest.raises(ConduitError):
        await phab_platform._conduit("user.search")
    assert client.post.await_count == 1


@pytest.mark.asyncio
async def test_create_inline_comment_params_and_id(monkeypatch) -> None:
    monkeypatch.setenv("PHABRICATOR_API_KEY", "api-key")
    client = _conduit_client((200, {"result": {"id": 77}, "error_code": None}))
    monkeypatch.setattr(phab_platform, "get_http_client", lambda: client)

    comment_id = await phab_platform.create_inline_comment(
        revision_id=1,
        diff_id=2,
        file_path="a.cpp",
        is_new_file=True,
        line_number=10,
        line_length=3,
        content="hi",
    )

    assert comment_id == 77
    (url,), kwargs = client.post.await_args
    assert url.endswith("/api/differential.createinline")
    assert json.loads(kwargs["data"]["params"]) == {
        "revisionID": 1,
        "diffID": 2,
        "filePath": "a.cpp",
        "isNewFile": True,
        "lineNumber": 10,
        "lineLength": 3,
        "content": "hi",
        "__conduit__": {"token": "api-key"},
    }


@pytest.mark.asyncio
async def test_create_revision_comment_params(monkeypatch) -> None:
    monkeypatch.setenv("PHABRICATOR_API_KEY", "api-key")
    client = _conduit_client((200, {"result": {}, "error_code": None}))
    monkeypatch.setattr(phab_platform, "get_http_client", lambda: client)

    await phab_platform.create_revision_comment(5, "msg")

    (url,), kwargs = client.post.await_args
    assert url.endswith("/api/differential.createcomment")
    assert json.loads(kwargs["data"]["params"]) == {
        "revision_id": 5,
        "attach_inlines": 1,
        "message": "msg",
        "__conduit__": {"token": "api-key"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "write",
    [
        lambda: phab_platform.create_inline_comment(1, 2, "a", True, 1, 0, "c"),
        lambda: phab_platform.create_revision_comment(1, "m"),
    ],
)
@pytest.mark.parametrize(
    "failure", [httpx.ReadTimeout("timeout"), (502, {})], ids=["timeout", "502"]
)
async def test_conduit_writes_are_not_retried(monkeypatch, write, failure) -> None:
    ok = (200, {"result": {"id": 1}, "error_code": None})
    if isinstance(failure, Exception):
        client = _conduit_client(ok)
        client.post.side_effect = [failure, *client.post.side_effect]
    else:
        client = _conduit_client(failure, ok)
    monkeypatch.setattr(phab_platform, "get_http_client", lambda: client)

    with pytest.raises(httpx.HTTPError):
        await write()
    assert client.post.await_count == 1


_REVISION = {
    "id": 12,
    "phid": "PHID-DREV-12",
    "fields": {"diffID": 34, "title": "Title", "policy": {"view": "public"}},
}
_DIFF_SEARCH = {
    "data": [
        {
            "id": 34,
            "phid": "PHID-DIFF-34",
            "fields": {
                "revisionPHID": "PHID-DREV-12",
                "dateCreated": 1700000000,
                "refs": [{"type": "base", "identifier": "abc"}],
            },
        }
    ]
}


def _recording_conduit(revisions: list[dict]):
    calls = []

    async def conduit(method, **params):
        calls.append((method, params))
        return {
            "differential.revision.search": {"data": revisions},
            "differential.diff.search": copy.deepcopy(_DIFF_SEARCH),
            "differential.getrawdiff": "raw diff",
        }[method]

    return conduit, calls


@pytest.mark.asyncio
async def test_load_from_revision_id(monkeypatch) -> None:
    conduit, calls = _recording_conduit([_REVISION])
    monkeypatch.setattr(phab_platform, "_conduit", conduit)

    patch = phab_platform.PhabricatorPatch(revision_id=12)
    assert await patch.is_accessible()
    await patch.load()

    assert calls == [
        ("differential.revision.search", {"constraints": {"ids": [12]}}),
        ("differential.diff.search", {"constraints": {"ids": [34]}}),
        ("differential.getrawdiff", {"diffID": 34}),
    ]
    assert patch.is_public()
    assert patch.patch_title == "Title"
    assert patch.raw_diff == "raw diff"
    assert patch.date_created == datetime.fromtimestamp(1700000000)
    assert patch._diff_metadata["refs"]["base"]["identifier"] == "abc"
    assert patch._diff_metadata["baseRevision"] == "abc"


@pytest.mark.asyncio
async def test_load_from_diff_id(monkeypatch) -> None:
    conduit, calls = _recording_conduit([_REVISION])
    monkeypatch.setattr(phab_platform, "_conduit", conduit)

    patch = phab_platform.PhabricatorPatch(diff_id=34)
    await patch.load()

    assert calls[-1] == (
        "differential.revision.search",
        {"constraints": {"phids": ["PHID-DREV-12"]}},
    )
    assert patch.revision_id == 12


@pytest.mark.asyncio
async def test_missing_revision_is_not_accessible(monkeypatch) -> None:
    conduit, _ = _recording_conduit([])
    monkeypatch.setattr(phab_platform, "_conduit", conduit)

    assert not await phab_platform.PhabricatorPatch(revision_id=12).is_accessible()


# ---------------------------------------------------------------------------
# Reviewer groups + project membership
# ---------------------------------------------------------------------------


def _fake_conduit(responses: dict):
    """Build a fake `_conduit()` returning a canned response per method."""

    async def conduit(method, **params):
        return responses[method]

    return conduit


@pytest.mark.asyncio
async def test_reviewer_phids_and_project_phids(monkeypatch) -> None:
    response = {
        "differential.revision.search": {
            "data": [
                {
                    "attachments": {
                        "reviewers": {
                            "reviewers": [
                                {"reviewerPHID": "PHID-USER-alice"},
                                {"reviewerPHID": "PHID-PROJ-ipprotection"},
                                {"reviewerPHID": "PHID-PROJ-homenewtab"},
                                {"reviewerPHID": None},
                            ]
                        }
                    }
                }
            ]
        }
    }
    monkeypatch.setattr(phab_platform, "_conduit", _fake_conduit(response))

    class FakePatch(phab_platform.PhabricatorPatch):
        def __init__(self):
            pass

        @property
        def revision_phid(self):
            return "PHID-DREV-test"

    patch = FakePatch()
    assert await patch.get_reviewer_phids() == [
        "PHID-USER-alice",
        "PHID-PROJ-ipprotection",
        "PHID-PROJ-homenewtab",
    ]
    assert await patch.get_reviewer_project_phids() == [
        "PHID-PROJ-ipprotection",
        "PHID-PROJ-homenewtab",
    ]


@pytest.mark.asyncio
async def test_resolve_project_phid(monkeypatch) -> None:
    phab_platform.resolve_project_phid.cache_clear()
    response = {"project.search": {"data": [{"phid": "PHID-PROJ-ipprotection"}]}}
    monkeypatch.setattr(phab_platform, "_conduit", _fake_conduit(response))
    assert (
        await phab_platform.resolve_project_phid("ip-protection-reviewers")
        == "PHID-PROJ-ipprotection"
    )
    phab_platform.resolve_project_phid.cache_clear()


@pytest.mark.asyncio
async def test_resolve_project_phid_not_found(monkeypatch) -> None:
    phab_platform.resolve_project_phid.cache_clear()
    monkeypatch.setattr(
        phab_platform, "_conduit", _fake_conduit({"project.search": {"data": []}})
    )
    assert await phab_platform.resolve_project_phid("does-not-exist") is None
    phab_platform.resolve_project_phid.cache_clear()


@pytest.mark.asyncio
async def test_get_project_members(monkeypatch) -> None:
    phab_platform.get_project_members.cache_clear()
    response = {
        "project.search": {
            "data": [
                {
                    "attachments": {
                        "members": {
                            "members": [
                                {"phid": "PHID-USER-alice"},
                                {"phid": "PHID-USER-bob"},
                            ]
                        }
                    }
                }
            ]
        }
    }
    monkeypatch.setattr(phab_platform, "_conduit", _fake_conduit(response))
    members = await phab_platform.get_project_members("PHID-PROJ-ipprotection")
    assert members == frozenset({"PHID-USER-alice", "PHID-USER-bob"})
    phab_platform.get_project_members.cache_clear()


@pytest.mark.asyncio
async def test_get_project_members_empty(monkeypatch) -> None:
    phab_platform.get_project_members.cache_clear()
    monkeypatch.setattr(
        phab_platform, "_conduit", _fake_conduit({"project.search": {"data": []}})
    )
    assert await phab_platform.get_project_members("PHID-PROJ-missing") == frozenset()
    phab_platform.get_project_members.cache_clear()


# ---------------------------------------------------------------------------
# Rotation recovery: historical_reviewer_project_phids
# ---------------------------------------------------------------------------


def _patch_with(current_reviewers, transactions):
    """A PhabricatorPatch whose current reviewers and transaction log are fixed."""

    class FakePatch(phab_platform.PhabricatorPatch):
        def __init__(self):
            pass

        @property
        def revision_id(self):
            return 308166

        async def get_reviewer_phids(self):
            return current_reviewers

        async def _get_transactions(self):
            return transactions

    return FakePatch()


# The exact reviewer event sequence from D308166: Herald adds the rotation group
# as a blocking reviewer, then phab-bot adds an individual and removes the group.
_D308166_TRANSACTIONS = [
    {
        "type": "reviewers",
        "fields": {
            "operations": [
                {
                    "operation": "add",
                    "phid": "PHID-PROJ-newtabrotation",
                    "isBlocking": True,
                }
            ]
        },
    },
    {
        "type": "reviewers",
        "fields": {
            "operations": [
                {"operation": "add", "phid": "PHID-USER-thecount"},
                {"operation": "remove", "phid": "PHID-PROJ-newtabrotation"},
            ]
        },
    },
    # The group is re-added as a *subscriber*, which must be ignored.
    {
        "type": "subscribers",
        "fields": {
            "operations": [{"operation": "add", "phid": "PHID-PROJ-newtabrotation"}]
        },
    },
]


@pytest.mark.asyncio
async def test_historical_recovers_rotation_group_removed_after_assignment() -> None:
    # By review time only the individual remains a reviewer.
    patch = _patch_with(["PHID-USER-thecount"], _D308166_TRANSACTIONS)
    assert await patch.get_reviewer_project_phids() == []
    assert await patch.get_historical_reviewer_project_phids() == [
        "PHID-PROJ-newtabrotation"
    ]


@pytest.mark.asyncio
async def test_historical_includes_current_groups_first() -> None:
    patch = _patch_with(
        ["PHID-PROJ-current", "PHID-USER-x"],
        [
            {
                "type": "reviewers",
                "fields": {
                    "operations": [
                        {"operation": "add", "phid": "PHID-PROJ-older"},
                    ]
                },
            }
        ],
    )
    # Current group first, then the historically-added one, deduped.
    assert await patch.get_historical_reviewer_project_phids() == [
        "PHID-PROJ-current",
        "PHID-PROJ-older",
    ]


@pytest.mark.asyncio
async def test_historical_ignores_users_and_remove_only() -> None:
    patch = _patch_with(
        [],
        [
            {
                "type": "reviewers",
                "fields": {
                    "operations": [
                        {"operation": "add", "phid": "PHID-USER-someone"},
                        {"operation": "remove", "phid": "PHID-PROJ-neveradded"},
                    ]
                },
            }
        ],
    )
    assert await patch.get_historical_reviewer_project_phids() == []


@pytest.mark.asyncio
async def test_historical_falls_back_on_transaction_error() -> None:
    class FakePatch(phab_platform.PhabricatorPatch):
        def __init__(self):
            pass

        @property
        def revision_id(self):
            return 1

        async def get_reviewer_phids(self):
            return ["PHID-PROJ-current"]

        async def _get_transactions(self):
            raise RuntimeError("conduit down")

    patch = FakePatch()
    # Degrades to the current snapshot rather than raising.
    assert await patch.get_historical_reviewer_project_phids() == ["PHID-PROJ-current"]


# ---------------------------------------------------------------------------
# PhabricatorPatch.github_repo_ref() -> review_context_repo/branch defaults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repo_callsign(monkeypatch) -> None:
    phab_platform._repo_callsign.cache_clear()
    response = {
        "diffusion.repository.search": {
            "data": [{"fields": {"callsign": "FIREFOXAUTOLAND"}}]
        }
    }
    monkeypatch.setattr(phab_platform, "_conduit", _fake_conduit(response))
    assert await phab_platform._repo_callsign("PHID-REPO-autoland") == "FIREFOXAUTOLAND"
    phab_platform._repo_callsign.cache_clear()


@pytest.mark.asyncio
async def test_repo_callsign_not_found(monkeypatch) -> None:
    phab_platform._repo_callsign.cache_clear()
    monkeypatch.setattr(
        phab_platform,
        "_conduit",
        _fake_conduit({"diffusion.repository.search": {"data": []}}),
    )
    assert await phab_platform._repo_callsign("PHID-REPO-missing") is None
    phab_platform._repo_callsign.cache_clear()


class _FakePatchWithRepo(phab_platform.PhabricatorPatch):
    def __init__(self, repository_phid=None):
        self._repository_phid = repository_phid

    @property
    def _revision_metadata(self):
        return {"fields": {"repositoryPHID": self._repository_phid}}


@pytest.mark.asyncio
@pytest.mark.parametrize("callsign", ["FIREFOXAUTOLAND", "FFTESTAUTOLANDDEV"])
async def test_github_repo_known_callsign(monkeypatch, callsign) -> None:
    phab_platform._repo_callsign.cache_clear()
    response = {
        "diffusion.repository.search": {"data": [{"fields": {"callsign": callsign}}]}
    }
    monkeypatch.setattr(phab_platform, "_conduit", _fake_conduit(response))

    patch = _FakePatchWithRepo("PHID-REPO-autoland")
    assert await patch.github_repo_ref() == ("mozilla-firefox/firefox", "autoland")
    phab_platform._repo_callsign.cache_clear()


@pytest.mark.asyncio
async def test_github_repo_unmapped_callsign(monkeypatch) -> None:
    phab_platform._repo_callsign.cache_clear()
    response = {
        "diffusion.repository.search": {
            "data": [{"fields": {"callsign": "COMMCENTRAL"}}]
        }
    }
    monkeypatch.setattr(phab_platform, "_conduit", _fake_conduit(response))

    patch = _FakePatchWithRepo("PHID-REPO-comm")
    assert await patch.github_repo_ref() is None
    phab_platform._repo_callsign.cache_clear()


@pytest.mark.asyncio
async def test_github_repo_no_repository_phid() -> None:
    patch = _FakePatchWithRepo(None)
    assert await patch.github_repo_ref() is None


def _inline_transaction(content: str = "", **inline_fields) -> dict:
    fields = {
        "diff": {"id": 1351682},
        "path": "browser/components/tabbrowser/docs/gbrowser.md",
        "line": 13,
        "length": 1,
        "replyToCommentPHID": None,
        "isDone": True,
    }
    fields.update(inline_fields)
    return {
        "id": 11098175,
        "type": "inline",
        "authorPHID": "PHID-USER-testauthor",
        "comments": [
            {
                "id": 1710710,
                "dateCreated": 1787105886,
                "dateModified": 1787133683,
                "removed": False,
                "content": {"raw": content},
            }
        ],
        "fields": fields,
    }


def _to_md(comments: list) -> str:
    """Render a revision whose comment timeline is the only thing that varies."""
    revision_metadata = {
        "id": 319190,
        "phid": "PHID-DREV-test",
        "fields": {
            "title": "A revision",
            "authorPHID": "PHID-USER-testauthor",
            "status": {"name": "Needs Review"},
            "uri": "https://phabricator.services.mozilla.com/D319190",
            "bugzilla.bug-id": "123456",
            "summary": "",
            "testPlan": "",
            "stackGraph": {},
        },
    }
    diff_metadata = {
        "id": 1351682,
        "dateCreated": 1787105886,
        "dateModified": 1787105886,
        "baseRevision": "abc123",
        "authorPHID": "PHID-USER-testauthor",
    }
    users_info = {
        "PHID-USER-testauthor": {
            "email": "author@mozilla.com",
            "real_name": "Test Author",
            "is_trusted": True,
            "is_trusted_bot": False,
        }
    }

    with (
        mock_patch.object(
            phab_platform.PhabricatorPatch, "load", new_callable=AsyncMock
        ),
        mock_patch.object(
            phab_platform.PhabricatorPatch,
            "_revision_metadata",
            revision_metadata,
            create=True,
        ),
        mock_patch.object(
            phab_platform.PhabricatorPatch, "_diff_metadata", diff_metadata, create=True
        ),
        mock_patch.object(
            phab_platform.PhabricatorPatch, "get_comments", return_value=comments
        ),
        mock_patch.object(phab_platform.PhabricatorPatch, "raw_diff", "diff content"),
        mock_patch(
            "bugbug.tools.core.platforms.phabricator._get_users_info_batch",
            return_value=users_info,
        ),
    ):
        return asyncio.run(phab_platform.PhabricatorPatch(diff_id=1351682).to_md())


def test_to_md_renders_suggestion_only_inline_comment() -> None:
    markdown = _to_md(
        [
            phab_platform.PhabricatorInlineComment(
                _inline_transaction(
                    hasSuggestion=True, suggestionText="the replacement"
                )
            )
        ]
    )

    assert "gbrowser.md` at Line 13" in markdown
    assert "```suggestion\nthe replacement\n```" in markdown


def test_to_md_keeps_inline_comment_with_neither_text_nor_suggestion() -> None:
    # The file and line alone tell a reader a review comment was left there.
    markdown = _to_md([phab_platform.PhabricatorInlineComment(_inline_transaction())])

    assert "gbrowser.md` at Line 13" in markdown


def test_to_md_drops_removed_inline_and_empty_general_comments() -> None:
    removed_inline = _inline_transaction(hasSuggestion=True, suggestionText="gone")
    removed_inline["comments"][0]["removed"] = True
    empty_general = _inline_transaction()
    empty_general["type"] = "comment"
    empty_general["fields"] = {}

    markdown = _to_md(
        [
            phab_platform.PhabricatorInlineComment(removed_inline),
            phab_platform.PhabricatorGeneralComment(empty_general),
        ]
    )

    assert "*No comments*" in markdown
    assert "gone" not in markdown


def test_sanitizing_untrusted_inline_comment_drops_its_suggestion() -> None:
    comment = phab_platform.PhabricatorInlineComment(
        _inline_transaction("some text", hasSuggestion=True, suggestionText="payload")
    )
    users_info = {
        comment.author_phid: {"is_trusted": False, "is_trusted_bot": False},
    }

    sanitized, filtered_count = phab_platform._sanitize_comments([comment], users_info)

    assert filtered_count == 1
    assert sanitized[0].content == phab_platform.UNTRUSTED_CONTENT_REDACTED
    assert sanitized[0].suggestion_text is None
    # The sanitizer works on a copy, so the original keeps its suggestion.
    assert comment.suggestion_text == "payload"
