# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from datetime import timedelta

from bugbug import phabricator


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
