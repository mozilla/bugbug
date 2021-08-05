# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest import mock

import responses

from bugbug.github import Github

github = Github(owner="webcompat", repo="web-bugs")
github.get_token = mock.Mock(return_value="mocked_token")  # type: ignore

TEST_URL = "https://api.github.com/repos/webcompat/web-bugs/issues"
TEST_EVENTS_URL = "https://api.github.com/repos/webcompat/web-bugs/issues/1/events"
HEADERS = {"link": "<https://api.github.com/test&page=2>; rel='next'"}
TEST_URL_SINGLE = "https://api.github.com/repos/webcompat/web-bugs/issues/71011"


def test_get_start_page() -> None:
    assert github.get_start_page() == 2


def test_fetch_issues() -> None:
    expected = [{"issue_id": "1", "events_url": TEST_EVENTS_URL}]
    expected_headers = {
        "next": {"url": "https://api.github.com/test&page=2", "rel": "next"}
    }

    # Mock main request
    responses.add(responses.GET, TEST_URL, json=expected, status=200, headers=HEADERS)

    # Assert that response without events has expected format
    response = github.fetch_issues(TEST_URL, False)
    assert response == (expected, expected_headers)


def test_fetch_issues_with_events() -> None:
    expected = [
        {"issue_id": "1", "events_url": TEST_EVENTS_URL, "labels": [{"name": "test"}]}
    ]
    expected_events = [{"event_id": "1"}]
    expected_headers = {
        "next": {"url": "https://api.github.com/test&page=2", "rel": "next"}
    }

    # Mock main request
    responses.add(responses.GET, TEST_URL, json=expected, status=200, headers=HEADERS)
    # Mock events request
    responses.add(responses.GET, TEST_EVENTS_URL, json=expected_events, status=200)

    expected_with_events = expected
    expected_with_events[0]["events"] = expected_events

    # Assert that response with events has expected format
    response_with_events = github.fetch_issues(TEST_URL, True)
    assert response_with_events == (expected_with_events, expected_headers)


def test_fetch_issues_empty_header() -> None:
    expected = [{"issue_id": "1", "events_url": TEST_EVENTS_URL}]

    # Mock main request with no headers
    responses.add(responses.GET, TEST_URL, json=expected, status=200)
    response_no_headers = github.fetch_issues(TEST_URL, False)

    assert response_no_headers == (expected, {})


def test_download_issues() -> None:
    expected = [{"issue_id": "1", "events_url": TEST_EVENTS_URL}]
    next_url_headers = {"link": "<https://api.github.com/test&page=3>; rel='next'"}

    # Make sure required requests are made as long as next link is present in the header
    responses.add(responses.GET, TEST_URL, json=expected, status=200, headers=HEADERS)
    responses.add(
        responses.GET,
        "https://api.github.com/test&page=2",
        json=expected,
        status=200,
        headers=next_url_headers,
    )
    responses.add(
        responses.GET, "https://api.github.com/test&page=3", json=expected, status=200
    )

    github.download_issues()


def test_download_issues_with_events() -> None:
    github.retrieve_events = True
    expected = [{"issue_id": "1", "events_url": TEST_EVENTS_URL}]
    expected_events = [{"event_id": "1"}]
    next_url_headers = {"link": "<https://api.github.com/test&page=3>; rel='next'"}

    # Make sure required requests are made as long as next link is present in the header
    responses.add(responses.GET, TEST_URL, json=expected, status=200, headers=HEADERS)
    responses.add(
        responses.GET,
        "https://api.github.com/test&page=2",
        json=expected,
        status=200,
        headers=next_url_headers,
    )
    responses.add(
        responses.GET, "https://api.github.com/test&page=3", json=expected, status=200
    )
    # Mock events request
    responses.add(responses.GET, TEST_EVENTS_URL, json=expected_events, status=200)

    github.download_issues()


def test_download_issues_updated_since_timestamp() -> None:
    github.retrieve_events = False
    first_page = [
        {"id": 30515129, "issue_id": "1"},
        {"id": 30536238, "issue_id": "2"},
        {"id": 35098369, "issue_id": "555"},
    ]

    second_page = [
        {"id": 305151291, "issue_id": "11"},
        {"id": 305362382, "issue_id": "21"},
        {"id": 350983693, "issue_id": "5551"},
    ]

    third_page = [
        {"id": 3051512912, "issue_id": "114"},
        {"id": 3053623823, "issue_id": "215"},
        {"id": 3509836934, "issue_id": "55516"},
    ]
    next_url_headers = {"link": "<https://api.github.com/test&page=3>; rel='next'"}

    # Make sure required requests are made as long as next link is present in the header
    since = (
        TEST_URL
        + "?state=all&since=2021-04-03T20%3A14%3A04%2B00%3A00&per_page=100&page=1"
    )

    # Make sure required requests are made as long as next link is present in the header
    responses.add(
        responses.GET,
        since,
        json=first_page,
        status=200,
        headers=HEADERS,
        match_querystring=True,
    )
    responses.add(
        responses.GET,
        "https://api.github.com/test&page=2",
        json=second_page,
        status=200,
        headers=next_url_headers,
    )
    responses.add(
        responses.GET, "https://api.github.com/test&page=3", json=third_page, status=200
    )

    result = first_page + second_page + third_page

    data = github.fetch_issues_updated_since_timestamp("2021-04-03T20:14:04+00:00")

    assert data == result


def test_fetch_issue_by_number() -> None:
    github.retrieve_events = False
    expected = [
        {"issue_id": "1", "events_url": TEST_EVENTS_URL, "labels": [{"name": "test"}]}
    ]
    expected_events = [{"event_id": "1"}]

    # Mock issue request and events request
    responses.add(responses.GET, TEST_URL_SINGLE, json=expected, status=200)
    responses.add(responses.GET, TEST_EVENTS_URL, json=expected_events, status=200)

    expected_with_events = expected
    expected_with_events[0]["events"] = expected_events

    data = github.fetch_issue_by_number("webcompat", "web-bugs", 71011, True)

    assert data == expected_with_events[0]
