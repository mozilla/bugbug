import asyncio
from unittest import mock

import pytest
import requests
from hackbot_agents.autowebcompat_etp import exceptions

EXISTING = [
    # Same resource and site, via wildcards on both sides.
    {
        "id": "a",
        "bugIds": ["1"],
        "category": "baseline",
        "urlPattern": "*://*.licdn.com/*",
        "topLevelUrlPattern": "*://*.example.com/*",
        "classifierFeatures": ["tracking-protection"],
        "last_modified": 123,
    },
    # A global exception covers every site.
    {"id": "b", "bugIds": ["2"], "urlPattern": "*://static.licdn.com/*"},
    # Same resource, different site.
    {
        "id": "c",
        "bugIds": ["3"],
        "urlPattern": "*://*.licdn.com/*",
        "topLevelUrlPattern": "*://other.org/*",
    },
    # Different resource; a shared suffix isn't a subdomain.
    {"id": "d", "bugIds": ["4"], "urlPattern": "*://notlicdn.com/*"},
]


def _ids(matches):
    return [match["id"] for match in matches]


def _patched(existing=EXISTING, **kwargs):
    response = mock.Mock(**{"json.return_value": {"data": existing}})
    return mock.patch.object(
        exceptions.requests, "get", return_value=response, **kwargs
    )


@pytest.mark.parametrize(
    "value,host",
    [
        ("*://*.licdn.com/*", "licdn.com"),
        ("https://static.licdn.com/sc/x.js", "static.licdn.com"),
        ("static.licdn.com", "static.licdn.com"),
        ("static.licdn.com/sc/x.js", "static.licdn.com"),
        ("*.licdn.com", "licdn.com"),
    ],
)
def test_to_host(value, host):
    assert exceptions.to_host(value) == host


def test_matching_by_resource_and_site():
    matches = exceptions.matching_exceptions(
        EXISTING, ["static.licdn.com"], ["www.example.com"]
    )
    assert _ids(matches) == ["a", "b"]
    # Only the fields worth showing, not Remote Settings metadata.
    assert "last_modified" not in matches[0]


def test_matching_any_site():
    assert _ids(exceptions.matching_exceptions(EXISTING, ["licdn.com"])) == [
        "a",
        "b",
        "c",
    ]


def test_matching_by_bug_id():
    assert _ids(exceptions.matching_exceptions(EXISTING, [], bug_id=3)) == ["c"]


def test_finds_overlapping_exceptions_for_records():
    record = {
        "urlPattern": "*://static.licdn.com/*",
        "topLevelUrlPattern": "*://www.example.com/*",
    }
    with _patched():
        assert _ids(exceptions.find_existing_exceptions([record])) == ["a", "b"]


def test_no_records_skips_the_fetch():
    with mock.patch.object(exceptions.requests, "get") as get:
        assert exceptions.find_existing_exceptions([]) == []
    get.assert_not_called()


def test_fetch_failure_returns_none():
    with mock.patch.object(
        exceptions.requests, "get", side_effect=requests.ConnectionError()
    ):
        assert exceptions.find_existing_exceptions([{"urlPattern": "*://a/*"}]) is None


def _call_tool(args):
    return asyncio.run(exceptions.find_etp_exceptions.handler(args))


def test_tool_finds_exceptions():
    with _patched():
        reply = _call_tool(
            {"hosts": ["https://static.licdn.com/x.js"], "sites": ["www.example.com"]}
        )
    text = reply["content"][0]["text"]
    assert "is_error" not in reply
    assert text.startswith("2 matching exception(s)")
    assert '"id": "a"' in text and '"id": "b"' in text


def test_tool_reports_no_match():
    with _patched():
        reply = _call_tool({"hosts": ["unrelated.net"]})
    assert reply["content"][0]["text"] == "No matching exceptions."


def test_tool_needs_hosts_or_bug():
    reply = _call_tool({"sites": ["www.example.com"]})
    assert reply["is_error"]


def test_tool_reports_fetch_failure():
    with mock.patch.object(
        exceptions.requests, "get", side_effect=requests.ConnectionError("down")
    ):
        reply = _call_tool({"bug_id": 1})
    assert reply["is_error"]
    assert "down" in reply["content"][0]["text"]
