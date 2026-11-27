# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re

import pytest
import responses

from bugbug_http import listener


class FakeMessage:
    def __init__(self, exchange: str) -> None:
        self.delivery_info = {"exchange": exchange}
        self.acked = False

    def ack(self) -> None:
        self.acked = True


def github_push(
    repository: str = "firefox-dev",
    ref: str = "refs/heads/user/foo/push-abc",
    deleted: bool = False,
) -> dict:
    return {
        "version": 1,
        "organization": "mozilla-firefox",
        "repository": repository,
        "installationId": 1,
        "eventId": "event",
        "tasks_for": "github-push",
        "branch": ref,
        "body": {
            "ref": ref,
            "before": "0" * 40,
            "after": "0" * 40 if deleted else "a" * 40,
            "deleted": deleted,
        },
    }


def hg_push(repo_url: str, user: str = "user@mozilla.com") -> dict:
    return {
        "payload": {
            "type": "changegroup.1",
            "data": {
                "repo_url": repo_url,
                "heads": ["b" * 40],
                "pushlog_pushes": [{"user": user}],
            },
        }
    }


def on_message(body: dict, exchange: str) -> list[str]:
    """Handle a message and return the URLs of the requests made to the service."""
    message = FakeMessage(exchange)
    listener._on_message(body, message)
    assert message.acked
    return [call.request.url for call in responses.calls]


@pytest.fixture(autouse=True)
def mock_schedules():
    responses.add(
        responses.GET,
        re.compile(rf"{listener.BUGBUG_HTTP_SERVER}/push/.+"),
        status=202,
        json={"ready": False},
    )


def test_github_push() -> None:
    assert on_message(github_push(), listener.GITHUB_PUSHES_EXCHANGE) == [
        f"{listener.BUGBUG_HTTP_SERVER}/push/firefox-dev/{'a' * 40}/schedules"
    ]


@pytest.mark.parametrize(
    "body",
    [
        github_push(deleted=True),
        github_push(ref="refs/tags/foo"),
        github_push(repository="firefox"),
    ],
)
def test_github_push_ignored(body: dict) -> None:
    assert on_message(body, listener.GITHUB_PUSHES_EXCHANGE) == []


def test_hg_push() -> None:
    assert on_message(
        hg_push("https://hg.mozilla.org/try"), listener.HG_PUSHES_EXCHANGE
    ) == [f"{listener.BUGBUG_HTTP_SERVER}/push/try/{'b' * 40}/schedules"]


@pytest.mark.parametrize(
    "body",
    [
        hg_push("https://hg.mozilla.org/mozilla-central"),
        hg_push("https://hg.mozilla.org/try", user="reviewbot"),
    ],
)
def test_hg_push_ignored(body: dict) -> None:
    assert on_message(body, listener.HG_PUSHES_EXCHANGE) == []
