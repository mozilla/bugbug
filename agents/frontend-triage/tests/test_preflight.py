"""Tests for the gate that stops a run on a bug whose fix is already written.

The payloads are what Bugzilla really returns -- the Phabricator stub carries
`is_patch: 0`, so a fixture that guessed would test nothing. Refresh with the
`curl` above each one.
"""

from hackbot_agents.frontend_triage import preflight
from hackbot_agents.frontend_triage.preflight import (
    BUG_FIELDS,
    attached_fix,
    fetch_bug,
)

# curl -s "https://bugzilla.mozilla.org/rest/bug?id=2066504&include_fields=\
# id,product,component,attachments.id,attachments.content_type,attachments.is_obsolete"
BUG_2066504 = {
    "id": 2066504,
    "product": "Firefox",
    "component": "New Tab Page",
    "attachments": [
        {
            "id": 9630702,
            "content_type": "text/x-phabricator-request",
            "is_obsolete": 0,
        }
    ],
}

# Same query for 2004297 -- an ordinary open bug, screenshot only.
BUG_2004297 = {
    "id": 2004297,
    "product": "Firefox",
    "component": "Tabbed Browser: Split View",
    "attachments": [{"id": 9525275, "content_type": "image/gif", "is_obsolete": 0}],
}


def test_bug_2066504_is_skipped_on_the_payload_bugzilla_really_returns():
    reason = attached_fix(BUG_2066504)
    assert reason is not None
    assert "9630702" in reason


def test_an_ordinary_open_bug_is_triaged():
    assert attached_fix(BUG_2004297) is None


def test_an_obsolete_revision_is_not_a_fix():
    superseded = {
        "attachments": [
            {"id": 1, "content_type": "text/x-phabricator-request", "is_obsolete": 1}
        ]
    }
    assert attached_fix(superseded) is None


def test_a_raw_patch_attachment_is_not_a_fix():
    # Deliberate: `is_patch` is set by whoever attaches, so a reporter's
    # speculative diff would suppress triage on a bug nobody is working.
    diff = {
        "attachments": [
            {"id": 2, "content_type": "text/plain", "is_patch": 1, "is_obsolete": 0}
        ]
    }
    assert attached_fix(diff) is None


def test_a_bug_that_could_not_be_read_is_triaged_rather_than_skipped():
    # `{}` is what `fetch_bug` returns for a broker failure or an inaccessible
    # bug; the rest are fields the proxy could drop or garble.
    assert attached_fix({}) is None
    assert attached_fix({"id": 1}) is None
    assert attached_fix({"attachments": None}) is None
    assert attached_fix({"attachments": []}) is None
    assert attached_fix({"attachments": [None, "not a dict", {}]}) is None
    assert attached_fix({"attachments": [{"content_type": None}]}) is None


def test_the_lookup_asks_for_the_id_and_for_attachments_without_their_data():
    assert BUG_FIELDS.startswith("id,")
    assert "attachments.content_type" in BUG_FIELDS
    assert "attachments.is_obsolete" in BUG_FIELDS
    assert ",attachments," not in BUG_FIELDS
    assert not BUG_FIELDS.endswith(",attachments")


async def test_a_broker_failure_triages_the_bug_rather_than_skipping_it(monkeypatch):
    assert await fetch_bug({}, 1) == {}
    assert await fetch_bug({"type": "http"}, 1) == {}

    def boom(url):
        raise ConnectionError("broker is down")

    monkeypatch.setattr(preflight, "streamablehttp_client", boom)
    assert await fetch_bug({"type": "http", "url": "http://broker/mcp"}, 1) == {}
