# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest import mock

import pytest
import requests
import responses

from bugbug.github import IssueDict
from scripts.github_issue_retriever import Retriever

github_issue_retriever = Retriever("webcompat", "web-bugs", "all", False, True)
github_issue_retriever.github.get_token = mock.Mock(return_value="mocked_token")  # type: ignore

PUBLIC_BODY = """
<p>Thanks for the report. We have closed this issue\n
automatically as we suspect it is invalid. If we made
a mistake, please\nfile a new issue and try to provide
more context.</p>\n
<!-- @private_url: https://github.com/webcompat/web-bugs-private/issues/12345 -->\n
"""


def test_replace_with_private() -> None:
    public_closed_issue = IssueDict(
        {"title": "Issue closed.", "body": PUBLIC_BODY, "id": 3456}
    )

    public_open_issue = IssueDict(
        {"title": "example.com - test issue", "body": "issue body", "id": 3457}
    )

    data = [
        public_closed_issue,
        public_open_issue,
    ]

    private_issue = IssueDict(
        {
            "title": "www.example.com - actual title",
            "body": "<p>Actual body</p>",
            "id": 1,
        }
    )
    # Mock private issue request
    responses.add(
        responses.GET,
        "https://api.github.com/repos/webcompat/web-bugs-private/issues/12345",
        json=private_issue,
        status=200,
    )

    expected = IssueDict(public_closed_issue.copy())
    expected["title"] = private_issue["title"]
    expected["body"] = private_issue["body"]

    (
        updated_issues,
        updated_ids,
    ) = github_issue_retriever.replace_with_private(data)

    assert len(updated_ids) == 1
    assert len(updated_issues) == 1
    assert len(data) == 2

    assert public_closed_issue["id"] in updated_ids
    # assert that public issue in the original list is changed
    assert data[0] == expected
    # assert that updated list contains an issue with private content
    assert updated_issues[0] == expected


def test_replace_missing_private() -> None:
    public_closed_issue_no_private = IssueDict(
        {"title": "Issue closed.", "body": "no private link", "id": 3459}
    )

    public_open_issue = IssueDict(
        {"title": "example.com - test issue 2", "body": "issue body", "id": 3458}
    )

    data = [public_closed_issue_no_private, public_open_issue]
    expected = IssueDict(public_closed_issue_no_private.copy())

    (
        updated_issues,
        updated_ids,
    ) = github_issue_retriever.replace_with_private(data)

    assert len(updated_ids) == 0
    assert len(updated_issues) == 0
    assert len(data) == 2
    assert data[0] == expected


def test_public_issues_with_deleted_private() -> None:
    public_closed_issue = IssueDict(
        {"title": "Issue closed.", "body": PUBLIC_BODY, "id": 3456}
    )

    public_closed_issue2 = IssueDict(
        {
            "title": "Issue closed.",
            "body": "<!-- @private_url: https://github.com/webcompat/web-bugs-private/issues/12346 -->",
            "id": 3457,
        }
    )

    data = [
        public_closed_issue,
        public_closed_issue2,
    ]

    private_issue = IssueDict(
        {
            "title": "www.example.com - actual title",
            "body": "<p>Actual body</p>",
            "id": 1,
        }
    )
    # Mock failed private issue request
    responses.add(
        responses.GET,
        "https://api.github.com/repos/webcompat/web-bugs-private/issues/12345",
        status=410,
    )

    # Mock successful private issue request
    responses.add(
        responses.GET,
        "https://api.github.com/repos/webcompat/web-bugs-private/issues/12346",
        json=private_issue,
        status=200,
    )

    expected = IssueDict(public_closed_issue.copy())

    expected2 = IssueDict(public_closed_issue2.copy())
    expected2["title"] = private_issue["title"]
    expected2["body"] = private_issue["body"]

    (
        updated_issues,
        updated_ids,
    ) = github_issue_retriever.replace_with_private(data)

    assert len(updated_ids) == 1
    assert len(updated_issues) == 1
    assert len(data) == 2

    assert public_closed_issue2["id"] in updated_ids
    # assert that public issue in the original list is unchanged
    assert data[0] == expected
    # assert that updated list contains an issue with private content
    assert updated_issues[0] == expected2


def test_public_issues_with_random_error() -> None:
    public_closed_issue = IssueDict(
        {"title": "Issue closed.", "body": PUBLIC_BODY, "id": 3456}
    )

    data = [public_closed_issue]

    # Mock failed private issue request
    responses.add(
        responses.GET,
        "https://api.github.com/repos/webcompat/web-bugs-private/issues/12345",
        status=500,
    )

    with pytest.raises(requests.adapters.MaxRetryError):
        github_issue_retriever.replace_with_private(data)
