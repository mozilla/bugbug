# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from typing import Any

import pytest

from bugbug import bugzilla


def test_get_bugs():
    all_bugs = {int(bug["id"]) for bug in bugzilla.get_bugs(include_invalid=True)}
    legitimate_bugs = {int(bug["id"]) for bug in bugzilla.get_bugs()}

    assert 1541482 in all_bugs
    assert 1541482 not in legitimate_bugs

    assert 1559674 in all_bugs
    assert 1559674 not in legitimate_bugs

    assert 1549207 in all_bugs
    assert 1549207 not in legitimate_bugs

    assert 1572747 in all_bugs
    assert 1572747 in legitimate_bugs


def test_get_bugs_include_all_products(monkeypatch: Any):
    monkeypatch.setattr(
        bugzilla.db,
        "read",
        lambda _: iter(
            [
                {"id": 1, "product": "Firefox"},
                {"id": 2, "product": "Firefox Graveyard"},
                {"id": 3, "product": "Invalid Bugs"},
            ]
        ),
    )

    default_bugs = [bug["id"] for bug in bugzilla.get_bugs()]
    all_product_bugs = [
        bug["id"] for bug in bugzilla.get_bugs(include_all_products=True)
    ]
    all_product_and_invalid_bugs = [
        bug["id"]
        for bug in bugzilla.get_bugs(include_all_products=True, include_invalid=True)
    ]

    assert default_bugs == [1]
    assert all_product_bugs == [1, 2]
    assert all_product_and_invalid_bugs == [1, 2, 3]


def test_get_fixed_versions():
    assert bugzilla.get_fixed_versions(
        {
            "target_milestone": "mozilla81",
            "cf_tracking_firefox83": "blocking",
            "cf_status_firefox82": "fixed",
            "cf_status_firefox81": "unaffected",
        }
    ) == [81, 82]

    assert bugzilla.get_fixed_versions(
        {
            "target_milestone": "mozilla82",
            "cf_tracking_firefox82": "---",
            "cf_status_firefox82": "fixed",
            "cf_status_firefox83": "fixed",
        }
    ) == [82, 83]

    assert bugzilla.get_fixed_versions(
        {
            "target_milestone": "mozilla82",
        }
    ) == [82]

    assert bugzilla.get_fixed_versions(
        {
            "target_milestone": "82 Branch",
        }
    ) == [82]

    assert bugzilla.get_fixed_versions(
        {
            "target_milestone": "Firefox 82",
        }
    ) == [82]


@pytest.fixture
def component_team_mapping():
    return {
        "products": [
            {
                "name": "JSS",
                "components": [
                    {
                        "name": "Library",
                        "team_name": "Crypto",
                    },
                    {
                        "name": "Tests",
                        "team_name": "Crypto",
                    },
                ],
            },
            {
                "name": "Core",
                "components": [
                    {
                        "name": "Graphics",
                        "team_name": "GFX",
                    },
                ],
            },
        ]
    }


def test_get_component_team_mapping(
    responses: Any, component_team_mapping: dict
) -> None:
    responses.add(
        responses.GET,
        "https://bugzilla.mozilla.org/rest/product?type=accessible&include_fields=name&include_fields=components.name&include_fields=components.team_name",
        status=200,
        json=component_team_mapping,
    )

    assert bugzilla.get_component_team_mapping() == {
        "Core": {"Graphics": "GFX"},
        "JSS": {"Library": "Crypto", "Tests": "Crypto"},
    }


def test_get_comments(responses: Any, monkeypatch: Any) -> None:
    responses.add(
        responses.GET,
        "https://bugzilla.mozilla.org/rest/bug/comment/11?comment_ids=12",
        status=200,
        json={
            "comments": {
                "11": {"id": 11, "bug_id": 101, "count": 1},
                "12": {"id": 12, "bug_id": 102, "count": 2},
            }
        },
    )
    bugs = {101: {"id": 101}, 102: {"id": 102}}
    monkeypatch.setattr(bugzilla, "get", lambda bug_ids: bugs)

    assert bugzilla.get_comments([11, 12, 11]) == {
        11: (bugs[101], {"id": 11, "bug_id": 101, "count": 1}),
        12: (bugs[102], {"id": 12, "bug_id": 102, "count": 2}),
    }


def test_get_comments_isolates_invalid_ids(responses: Any, monkeypatch: Any) -> None:
    responses.add(
        responses.GET,
        "https://bugzilla.mozilla.org/rest/bug/comment/11?comment_ids=999",
        status=400,
        json={"code": 111, "message": "Invalid comment ID"},
    )
    responses.add(
        responses.GET,
        "https://bugzilla.mozilla.org/rest/bug/comment/11",
        status=200,
        json={"comments": {"11": {"id": 11, "bug_id": 101, "count": 1}}},
    )
    responses.add(
        responses.GET,
        "https://bugzilla.mozilla.org/rest/bug/comment/999",
        status=400,
        json={"code": 111, "message": "Invalid comment ID"},
    )
    bug = {"id": 101}
    monkeypatch.setattr(bugzilla, "get", lambda bug_ids: {101: bug})

    assert bugzilla.get_comments([11, 999]) == {
        11: (bug, {"id": 11, "bug_id": 101, "count": 1})
    }


def test_get_comments_omits_private_comments(responses: Any, monkeypatch: Any) -> None:
    responses.add(
        responses.GET,
        "https://bugzilla.mozilla.org/rest/bug/comment/11?comment_ids=12",
        status=400,
        json={"code": 110, "message": "Comment is private"},
    )
    responses.add(
        responses.GET,
        "https://bugzilla.mozilla.org/rest/bug/comment/11",
        status=200,
        json={"comments": {"11": {"id": 11, "bug_id": 101, "count": 1}}},
    )
    responses.add(
        responses.GET,
        "https://bugzilla.mozilla.org/rest/bug/comment/12",
        status=400,
        json={"code": 110, "message": "Comment is private"},
    )
    bug = {"id": 101}
    monkeypatch.setattr(bugzilla, "get", lambda bug_ids: {101: bug})

    assert bugzilla.get_comments([11, 12]) == {
        11: (bug, {"id": 11, "bug_id": 101, "count": 1})
    }


def test_comment_downloads_include_collapsed_comments() -> None:
    assert "_collapsed_comments" in bugzilla.COMMENT_INCLUDE_FIELDS
