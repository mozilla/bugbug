# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest import mock

import responses

from bugbug import github

github.get_token = mock.Mock(return_value="mocked_token")

TEST_URL = "https://api.github.com/repos/webcompat/web-bugs/issues"
TEST_EVENTS_URL = "https://api.github.com/repos/webcompat/web-bugs/issues/1/events"
HEADERS = {"link": "<https://api.github.com/test&page=2>; rel='next'"}


def test_get_start_page():
    assert github.get_start_page() == 2


def test_fetch_issues():
    expected = [{"issue_id": "1", "events_url": TEST_EVENTS_URL}]
    expected_headers = {
        "next": {"url": "https://api.github.com/test&page=2", "rel": "next"}
    }

    # Mock main request
    responses.add(responses.GET, TEST_URL, json=expected, status=200, headers=HEADERS)

    # Assert that response without events has expected format
    response = github.fetch_issues(TEST_URL, False)
    assert response == (expected, expected_headers)


def test_fetch_issues_with_events():
    expected = [{"issue_id": "1", "events_url": TEST_EVENTS_URL}]
    expected_events = [{"event_id": "1"}]
    expected_headers = {
        "next": {"url": "https://api.github.com/test&page=2", "rel": "next"}
    }

    # Mock main request
    responses.add(responses.GET, TEST_URL, json=expected, status=200, headers=HEADERS)
    # Mock events request
    responses.add(responses.GET, TEST_EVENTS_URL, json=expected_events, status=200)

    # Assert that response with events has expected format
    response_with_events = github.fetch_issues(TEST_URL, True)
    expected_with_events = expected
    expected_with_events[0]["events"] = expected_events

    assert response_with_events == (expected_with_events, expected_headers)


def test_fetch_issues_empty_header():
    expected = [{"issue_id": "1", "events_url": TEST_EVENTS_URL}]

    # Mock main request with no headers
    responses.add(responses.GET, TEST_URL, json=expected, status=200)
    response_no_headers = github.fetch_issues(TEST_URL, False)

    assert response_no_headers == (expected, {})


def test_download_issues():
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

    github.download_issues("webcompat", "web-bugs", "all")
