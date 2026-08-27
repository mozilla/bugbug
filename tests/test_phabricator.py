# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from datetime import timedelta
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

import pytest

from bugbug import phabricator
from bugbug.tools.core.platforms import phabricator as phab_platform


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
# Reviewer groups + project membership
# ---------------------------------------------------------------------------


def _fake_client(responses: dict) -> MagicMock:
    """Build a fake client whose .request(method, ...) returns a canned response."""
    client = MagicMock()
    client.request.side_effect = lambda method, **kwargs: responses[method]
    return client


def test_reviewer_phids_and_project_phids(monkeypatch) -> None:
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
    monkeypatch.setattr(
        phab_platform, "get_phabricator_client", lambda: _fake_client(response)
    )

    class FakePatch(phab_platform.PhabricatorPatch):
        def __init__(self):
            pass

        @property
        def revision_phid(self):
            return "PHID-DREV-test"

    patch = FakePatch()
    assert patch.reviewer_phids == [
        "PHID-USER-alice",
        "PHID-PROJ-ipprotection",
        "PHID-PROJ-homenewtab",
    ]
    assert patch.reviewer_project_phids == [
        "PHID-PROJ-ipprotection",
        "PHID-PROJ-homenewtab",
    ]


def test_resolve_project_phid(monkeypatch) -> None:
    phab_platform.resolve_project_phid.cache_clear()
    response = {"project.search": {"data": [{"phid": "PHID-PROJ-ipprotection"}]}}
    monkeypatch.setattr(
        phab_platform, "get_phabricator_client", lambda: _fake_client(response)
    )
    assert (
        phab_platform.resolve_project_phid("ip-protection-reviewers")
        == "PHID-PROJ-ipprotection"
    )
    phab_platform.resolve_project_phid.cache_clear()


def test_resolve_project_phid_not_found(monkeypatch) -> None:
    phab_platform.resolve_project_phid.cache_clear()
    monkeypatch.setattr(
        phab_platform,
        "get_phabricator_client",
        lambda: _fake_client({"project.search": {"data": []}}),
    )
    assert phab_platform.resolve_project_phid("does-not-exist") is None
    phab_platform.resolve_project_phid.cache_clear()


def test_get_project_members(monkeypatch) -> None:
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
    monkeypatch.setattr(
        phab_platform, "get_phabricator_client", lambda: _fake_client(response)
    )
    members = phab_platform.get_project_members("PHID-PROJ-ipprotection")
    assert members == frozenset({"PHID-USER-alice", "PHID-USER-bob"})
    phab_platform.get_project_members.cache_clear()


def test_get_project_members_empty(monkeypatch) -> None:
    phab_platform.get_project_members.cache_clear()
    monkeypatch.setattr(
        phab_platform,
        "get_phabricator_client",
        lambda: _fake_client({"project.search": {"data": []}}),
    )
    assert phab_platform.get_project_members("PHID-PROJ-missing") == frozenset()
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

        @property
        def reviewer_phids(self):
            return current_reviewers

        def _get_transactions(self):
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


def test_historical_recovers_rotation_group_removed_after_assignment() -> None:
    # By review time only the individual remains a reviewer.
    patch = _patch_with(["PHID-USER-thecount"], _D308166_TRANSACTIONS)
    assert patch.reviewer_project_phids == []
    assert patch.historical_reviewer_project_phids == ["PHID-PROJ-newtabrotation"]


def test_historical_includes_current_groups_first() -> None:
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
    assert patch.historical_reviewer_project_phids == [
        "PHID-PROJ-current",
        "PHID-PROJ-older",
    ]


def test_historical_ignores_users_and_remove_only() -> None:
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
    assert patch.historical_reviewer_project_phids == []


def test_historical_falls_back_on_transaction_error() -> None:
    class FakePatch(phab_platform.PhabricatorPatch):
        def __init__(self):
            pass

        @property
        def revision_id(self):
            return 1

        @property
        def reviewer_phids(self):
            return ["PHID-PROJ-current"]

        def _get_transactions(self):
            raise RuntimeError("conduit down")

    patch = FakePatch()
    # Degrades to the current snapshot rather than raising.
    assert patch.historical_reviewer_project_phids == ["PHID-PROJ-current"]


# ---------------------------------------------------------------------------
# PhabricatorPatch.github_repo_ref() -> review_context_repo/branch defaults
# ---------------------------------------------------------------------------


def test_repo_callsign(monkeypatch) -> None:
    phab_platform._repo_callsign.cache_clear()
    response = {
        "diffusion.repository.search": {
            "data": [{"fields": {"callsign": "FIREFOXAUTOLAND"}}]
        }
    }
    monkeypatch.setattr(
        phab_platform, "get_phabricator_client", lambda: _fake_client(response)
    )
    assert phab_platform._repo_callsign("PHID-REPO-autoland") == "FIREFOXAUTOLAND"
    phab_platform._repo_callsign.cache_clear()


def test_repo_callsign_not_found(monkeypatch) -> None:
    phab_platform._repo_callsign.cache_clear()
    monkeypatch.setattr(
        phab_platform,
        "get_phabricator_client",
        lambda: _fake_client({"diffusion.repository.search": {"data": []}}),
    )
    assert phab_platform._repo_callsign("PHID-REPO-missing") is None
    phab_platform._repo_callsign.cache_clear()


class _FakePatchWithRepo(phab_platform.PhabricatorPatch):
    def __init__(self, repository_phid=None):
        self._repository_phid = repository_phid

    @property
    def _revision_metadata(self):
        return {"fields": {"repositoryPHID": self._repository_phid}}


@pytest.mark.asyncio
async def test_github_repo_known_callsign(monkeypatch) -> None:
    phab_platform._repo_callsign.cache_clear()
    response = {
        "diffusion.repository.search": {
            "data": [{"fields": {"callsign": "FIREFOXAUTOLAND"}}]
        }
    }
    monkeypatch.setattr(
        phab_platform, "get_phabricator_client", lambda: _fake_client(response)
    )

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
    monkeypatch.setattr(
        phab_platform, "get_phabricator_client", lambda: _fake_client(response)
    )

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
            phab_platform.PhabricatorPatch, "_revision_metadata", revision_metadata
        ),
        mock_patch.object(
            phab_platform.PhabricatorPatch, "_diff_metadata", diff_metadata
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
        return phab_platform.PhabricatorPatch(diff_id=1351682).to_md()


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
