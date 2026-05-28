# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from datetime import timedelta
from unittest.mock import MagicMock

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
