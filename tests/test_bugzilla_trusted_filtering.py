"""Test Bugzilla trusted user filtering functionality."""

import os

import pytest
import responses

from bugbug import bugzilla as bugzilla_module
from bugbug.tools.core.platforms.bugzilla import (
    _check_users_batch,
    _sanitize_timeline_items,
    create_bug_timeline,
)


@pytest.mark.skipif(
    not os.environ.get("BUGZILLA_TOKEN"),
    reason="Requires BUGZILLA_TOKEN for authenticated API access",
)
@pytest.mark.withoutresponses
def test_trusted_check():
    """Test trusted user verification via Bugzilla API."""
    bugzilla_module.set_token(os.environ["BUGZILLA_TOKEN"])
    results = _check_users_batch(["padenot@mozilla.com"])
    assert results["padenot@mozilla.com"] is True
    results = _check_users_batch(["lkasdjflksjdfljsldjflsjdlfskldfj@mozilla.com"])
    assert results["lkasdjflksjdfljsldjflsjdlfskldfj@mozilla.com"] is False


@responses.activate
def test_token_set_on_bugzilla_base():
    """Test that set_token() sets the token on BugzillaBase, not just Bugzilla."""
    from libmozdata.bugzilla import Bugzilla, BugzillaBase, BugzillaUser

    old_token = BugzillaBase.TOKEN
    try:
        bugzilla_module.set_token("test_token_12345")

        # Verify token is set on BugzillaBase (parent class)
        assert BugzillaBase.TOKEN == "test_token_12345"
        # Verify both child classes inherit it
        assert Bugzilla.TOKEN == "test_token_12345"
        assert BugzillaUser.TOKEN == "test_token_12345"

        # Mock trusted user response (with mozilla-corporation group)
        responses.add(
            responses.GET,
            "https://bugzilla.mozilla.org/rest/user",
            json={
                "users": [
                    {
                        "name": "trusted@mozilla.com",
                        "groups": [
                            {"id": 42, "name": "mozilla-corporation"},
                            {"id": 69, "name": "everyone"},
                        ],
                    }
                ],
                "faults": [],
            },
            status=200,
        )

        # Mock untrusted user response (no mozilla-corporation group)
        responses.add(
            responses.GET,
            "https://bugzilla.mozilla.org/rest/user",
            json={
                "users": [
                    {
                        "name": "untrusted@example.com",
                        "groups": [{"id": 69, "name": "everyone"}],
                    }
                ],
                "faults": [],
            },
            status=200,
        )

        # Mock non-existent user response (faulted user)
        responses.add(
            responses.GET,
            "https://bugzilla.mozilla.org/rest/user",
            json={
                "users": [],
                "faults": [{"name": "nonexistent@example.com", "faultCode": 51}],
            },
            status=200,
        )

        # Test trusted user
        results = _check_users_batch(["trusted@mozilla.com"])
        assert results["trusted@mozilla.com"] is True

        # Test untrusted user
        results = _check_users_batch(["untrusted@example.com"])
        assert results["untrusted@example.com"] is False

        # Test non-existent user (should not raise, should return False)
        results = _check_users_batch(["nonexistent@example.com"])
        assert results["nonexistent@example.com"] is False

    finally:
        BugzillaBase.TOKEN = old_token


def test_trusted_check_without_token():
    """Test that trusted check raises error without Bugzilla token."""
    from libmozdata.bugzilla import BugzillaBase

    old_token = BugzillaBase.TOKEN
    BugzillaBase.TOKEN = None

    try:
        with pytest.raises(ValueError, match="Bugzilla token required"):
            _check_users_batch(["test@example.com"])
    finally:
        BugzillaBase.TOKEN = old_token


def test_trusted_check_empty_email():
    """Test that empty email list returns empty results."""
    results = _check_users_batch([])
    assert results == {}


def test_untrusted_before_last_trusted():
    """Test that untrusted content before last trusted COMMENT is included.

    Logic: Walk backwards to find last trusted COMMENT. Everything before it
    is included (validated by trusted user). Everything after is filtered.
    Only comments imply content review, not metadata changes.
    """
    # Mock trusted status
    cache = {
        "trusted@mozilla.com": True,
        "untrusted@example.com": False,
    }

    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "untrusted@example.com",
            "id": 1,
            "count": 0,
            "text": "Untrusted comment before last trusted",
        },
        {
            "time": "2024-01-01T10:01:00Z",
            "author": "trusted@mozilla.com",
            "id": 2,
            "count": 1,
            "text": "Last trusted comment",
        },
        {
            "time": "2024-01-01T10:02:00Z",
            "author": "untrusted@example.com",
            "id": 3,
            "count": 2,
            "text": "Untrusted comment after last trusted",
        },
    ]

    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(comments, [], cache)
    )
    timeline = create_bug_timeline(sanitized_comments, sanitized_history)

    # Comment before last trusted comment should be kept (trusted user saw it)
    # Comment after last trusted comment should be filtered
    assert filtered_comments == 1
    assert "Untrusted comment before last trusted" in "\n".join(timeline)
    assert "Untrusted comment after last trusted" not in "\n".join(timeline)
    assert "[Content from untrusted user removed for security]" in "\n".join(timeline)


def test_no_trusted_users():
    """Test that all untrusted comments are filtered when there's no trusted activity."""
    # Mock trusted status
    cache = {
        "untrusted1@example.com": False,
        "untrusted2@example.com": False,
    }

    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "untrusted1@example.com",
            "id": 1,
            "count": 0,
            "text": "First untrusted comment",
        },
        {
            "time": "2024-01-01T10:01:00Z",
            "author": "untrusted2@example.com",
            "id": 2,
            "count": 1,
            "text": "Second untrusted comment",
        },
    ]

    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(comments, [], cache)
    )
    timeline = create_bug_timeline(sanitized_comments, sanitized_history)

    # All comments should be filtered when there's no trusted activity
    assert filtered_comments == 2
    assert "First untrusted comment" not in "\n".join(timeline)
    assert "Second untrusted comment" not in "\n".join(timeline)
    timeline_text = "\n".join(timeline)
    assert (
        timeline_text.count("[Content from untrusted user removed for security]") == 2
    )


def test_fail_closed_scenarios():
    """Test that the system raises exceptions on various error conditions."""
    from unittest.mock import patch

    import pytest
    from libmozdata.bugzilla import BugzillaBase

    from bugbug.tools.core.platforms.bugzilla import _check_users_batch

    test_emails = ["test@example.com"]

    # Set a dummy token for testing
    old_token = BugzillaBase.TOKEN
    BugzillaBase.TOKEN = "dummy_token"

    try:
        # Test OSError
        with patch(
            "libmozdata.bugzilla.BugzillaUser", side_effect=OSError("Network error")
        ):
            with pytest.raises(OSError):
                _check_users_batch(test_emails)

        # Test TimeoutError
        with patch(
            "libmozdata.bugzilla.BugzillaUser", side_effect=TimeoutError("Timeout")
        ):
            with pytest.raises(TimeoutError):
                _check_users_batch(test_emails)

        # Test ConnectionError
        with patch(
            "libmozdata.bugzilla.BugzillaUser",
            side_effect=ConnectionError("Connection failed"),
        ):
            with pytest.raises(ConnectionError):
                _check_users_batch(test_emails)

        # Test HTTPError
        from requests.exceptions import HTTPError

        with patch(
            "libmozdata.bugzilla.BugzillaUser",
            side_effect=HTTPError("HTTP 500"),
        ):
            with pytest.raises(HTTPError):
                _check_users_batch(test_emails)

        # Test RequestException
        from requests.exceptions import RequestException

        with patch(
            "libmozdata.bugzilla.BugzillaUser",
            side_effect=RequestException("Request failed"),
        ):
            with pytest.raises(RequestException):
                _check_users_batch(test_emails)

        # Test JSONDecodeError
        from json import JSONDecodeError

        with patch(
            "libmozdata.bugzilla.BugzillaUser",
            side_effect=JSONDecodeError("Invalid JSON", "", 0),
        ):
            with pytest.raises(JSONDecodeError):
                _check_users_batch(test_emails)

        # Test KeyError
        with patch(
            "libmozdata.bugzilla.BugzillaUser",
            side_effect=KeyError("Missing key"),
        ):
            with pytest.raises(KeyError):
                _check_users_batch(test_emails)

        # Test ValueError
        with patch(
            "libmozdata.bugzilla.BugzillaUser",
            side_effect=ValueError("Invalid value"),
        ):
            with pytest.raises(ValueError):
                _check_users_batch(test_emails)
    finally:
        BugzillaBase.TOKEN = old_token


def test_timeline_variation_alternating():
    """Test: Untrusted → Trusted1 → Untrusted → Trusted2 → Untrusted."""
    cache = {
        "trusted1@mozilla.com": True,
        "trusted2@mozilla.com": True,
        "untrusted@example.com": False,
    }

    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "untrusted@example.com",
            "id": 1,
            "count": 0,
            "text": "Untrusted comment 1",
        },
        {
            "time": "2024-01-01T10:01:00Z",
            "author": "trusted1@mozilla.com",
            "id": 2,
            "count": 1,
            "text": "Trusted comment 1",
        },
        {
            "time": "2024-01-01T10:02:00Z",
            "author": "untrusted@example.com",
            "id": 3,
            "count": 2,
            "text": "Untrusted comment 2",
        },
        {
            "time": "2024-01-01T10:03:00Z",
            "author": "trusted2@mozilla.com",
            "id": 4,
            "count": 3,
            "text": "Trusted comment 2",
        },
        {
            "time": "2024-01-01T10:04:00Z",
            "author": "untrusted@example.com",
            "id": 5,
            "count": 4,
            "text": "Untrusted comment 3",
        },
    ]

    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(comments, [], cache)
    )
    timeline = create_bug_timeline(sanitized_comments, sanitized_history)

    timeline_text = "\n".join(timeline)

    # Everything before trusted2 (the last trusted user) should be included
    assert "Untrusted comment 1" in timeline_text
    assert "Trusted comment 1" in timeline_text
    assert "Untrusted comment 2" in timeline_text
    assert "Trusted comment 2" in timeline_text

    # Only the last untrusted comment (after last trusted) should be filtered
    assert "Untrusted comment 3" not in timeline_text
    assert filtered_comments == 1


def test_timeline_variation_trusted_first():
    """Test: Trusted user as first item."""
    cache = {
        "trusted@mozilla.com": True,
        "untrusted@example.com": False,
    }

    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "trusted@mozilla.com",
            "id": 1,
            "count": 0,
            "text": "Trusted comment first",
        },
        {
            "time": "2024-01-01T10:01:00Z",
            "author": "untrusted@example.com",
            "id": 2,
            "count": 1,
            "text": "Untrusted comment after",
        },
    ]

    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(comments, [], cache)
    )
    timeline = create_bug_timeline(sanitized_comments, sanitized_history)

    timeline_text = "\n".join(timeline)

    # Trusted comment is included
    assert "Trusted comment first" in timeline_text

    # Untrusted comment after last trusted should be filtered
    assert "Untrusted comment after" not in timeline_text
    assert filtered_comments == 1


def test_timeline_variation_trusted_last():
    """Test: Trusted user as last item."""
    cache = {
        "trusted@mozilla.com": True,
        "untrusted@example.com": False,
    }

    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "untrusted@example.com",
            "id": 1,
            "count": 0,
            "text": "Untrusted comment first",
        },
        {
            "time": "2024-01-01T10:01:00Z",
            "author": "trusted@mozilla.com",
            "id": 2,
            "count": 1,
            "text": "Trusted comment last",
        },
    ]

    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(comments, [], cache)
    )
    timeline = create_bug_timeline(sanitized_comments, sanitized_history)

    timeline_text = "\n".join(timeline)

    # All comments should be included because trusted user is last
    assert "Untrusted comment first" in timeline_text
    assert "Trusted comment last" in timeline_text
    assert filtered_comments == 0


def test_timeline_variation_all_trusted():
    """Test: All trusted users (no filtering needed)."""
    cache = {
        "trusted1@mozilla.com": True,
        "trusted2@mozilla.com": True,
    }

    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "trusted1@mozilla.com",
            "id": 1,
            "count": 0,
            "text": "Trusted comment 1",
        },
        {
            "time": "2024-01-01T10:01:00Z",
            "author": "trusted2@mozilla.com",
            "id": 2,
            "count": 1,
            "text": "Trusted comment 2",
        },
    ]

    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(comments, [], cache)
    )
    timeline = create_bug_timeline(sanitized_comments, sanitized_history)

    timeline_text = "\n".join(timeline)

    # All comments should be included
    assert "Trusted comment 1" in timeline_text
    assert "Trusted comment 2" in timeline_text
    assert filtered_comments == 0


def test_timeline_variation_multiple_trusted_positions():
    """Test: Multiple trusted users at different positions."""
    cache = {
        "trusted1@mozilla.com": True,
        "trusted2@mozilla.com": True,
        "trusted3@mozilla.com": True,
        "untrusted@example.com": False,
    }

    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "trusted1@mozilla.com",
            "id": 1,
            "count": 0,
            "text": "Trusted comment 1",
        },
        {
            "time": "2024-01-01T10:01:00Z",
            "author": "untrusted@example.com",
            "id": 2,
            "count": 1,
            "text": "Untrusted comment 1",
        },
        {
            "time": "2024-01-01T10:02:00Z",
            "author": "trusted2@mozilla.com",
            "id": 3,
            "count": 2,
            "text": "Trusted comment 2",
        },
        {
            "time": "2024-01-01T10:03:00Z",
            "author": "untrusted@example.com",
            "id": 4,
            "count": 3,
            "text": "Untrusted comment 2",
        },
        {
            "time": "2024-01-01T10:04:00Z",
            "author": "trusted3@mozilla.com",
            "id": 5,
            "count": 4,
            "text": "Trusted comment 3",
        },
        {
            "time": "2024-01-01T10:05:00Z",
            "author": "untrusted@example.com",
            "id": 6,
            "count": 5,
            "text": "Untrusted comment 3",
        },
    ]

    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(comments, [], cache)
    )
    timeline = create_bug_timeline(sanitized_comments, sanitized_history)

    timeline_text = "\n".join(timeline)

    # Everything before trusted3 (last trusted) should be included
    assert "Trusted comment 1" in timeline_text
    assert "Untrusted comment 1" in timeline_text
    assert "Trusted comment 2" in timeline_text
    assert "Untrusted comment 2" in timeline_text
    assert "Trusted comment 3" in timeline_text

    # Only last untrusted comment should be filtered
    assert "Untrusted comment 3" not in timeline_text
    assert filtered_comments == 1


def test_trusted_history_does_not_validate():
    """Test that trusted user history events do NOT validate prior content.

    Only COMMENTS from trusted users imply content review. Metadata changes
    (status, assignee, etc.) do not imply the user reviewed all prior comments.
    """
    cache = {
        "trusted@mozilla.com": True,
        "untrusted@example.com": False,
    }

    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "untrusted@example.com",
            "id": 1,
            "count": 0,
            "text": "Untrusted comment 1",
        },
    ]

    history = [
        {
            "when": "2024-01-01T10:01:00Z",
            "who": "trusted@mozilla.com",
            "changes": [
                {"field_name": "status", "removed": "NEW", "added": "ASSIGNED"}
            ],
        },
        {
            "when": "2024-01-01T10:02:00Z",
            "who": "untrusted@example.com",
            "changes": [{"field_name": "priority", "removed": "P3", "added": "P2"}],
        },
    ]

    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(comments, history, cache)
    )
    timeline = create_bug_timeline(sanitized_comments, sanitized_history)

    timeline_text = "\n".join(timeline)

    # Trusted history event does NOT validate the untrusted comment before it
    # Since there's no trusted COMMENT, all untrusted content should be filtered
    assert "Untrusted comment 1" not in timeline_text
    assert filtered_comments == 1

    # Trusted history events are always included (they're metadata, not user content)
    assert "status" in timeline_text.lower()
    assert "ASSIGNED" in timeline_text

    # Untrusted history after (no trusted comment ever) should be filtered
    assert filtered_history == 1


def test_trusted_comment_validates_before_untrusted_history_after():
    """Test that trusted COMMENT validates prior content but filters untrusted history after."""
    cache = {
        "trusted@mozilla.com": True,
        "untrusted@example.com": False,
    }

    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "untrusted@example.com",
            "id": 1,
            "count": 0,
            "text": "Untrusted comment before trusted comment",
        },
        {
            "time": "2024-01-01T10:01:00Z",
            "author": "trusted@mozilla.com",
            "id": 2,
            "count": 1,
            "text": "Trusted comment - validates prior content",
        },
    ]

    history = [
        {
            "when": "2024-01-01T10:02:00Z",
            "who": "untrusted@example.com",
            "changes": [{"field_name": "priority", "removed": "P3", "added": "P1"}],
        },
    ]

    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(comments, history, cache)
    )
    timeline = create_bug_timeline(sanitized_comments, sanitized_history)

    timeline_text = "\n".join(timeline)

    # Untrusted comment BEFORE trusted comment should be included
    assert "Untrusted comment before trusted comment" in timeline_text
    assert filtered_comments == 0

    # Trusted comment should be included
    assert "Trusted comment - validates prior content" in timeline_text

    # Untrusted history AFTER trusted comment should be filtered
    assert filtered_history == 1
    assert "[Filtered]" in timeline_text


@responses.activate
def test_editbugs_with_recent_activity_is_trusted():
    """Test that users with editbugs and recent activity are trusted."""
    from datetime import datetime, timezone

    from libmozdata.bugzilla import BugzillaBase

    old_token = BugzillaBase.TOKEN
    try:
        bugzilla_module.set_token("test_token")

        # User with editbugs who was seen recently (today)
        recent_date = datetime.now(timezone.utc).isoformat()
        responses.add(
            responses.GET,
            "https://bugzilla.mozilla.org/rest/user",
            json={
                "users": [
                    {
                        "name": "editbugs@example.com",
                        "groups": [
                            {"id": 9, "name": "editbugs"},
                            {"id": 69, "name": "everyone"},
                        ],
                        "last_seen_date": recent_date,
                    }
                ],
                "faults": [],
            },
            status=200,
        )

        results = _check_users_batch(["editbugs@example.com"])
        assert results["editbugs@example.com"] is True

    finally:
        BugzillaBase.TOKEN = old_token


@responses.activate
def test_editbugs_with_stale_activity_is_untrusted():
    """Test that users with editbugs but stale activity (>365 days) are not trusted."""
    from libmozdata.bugzilla import BugzillaBase

    old_token = BugzillaBase.TOKEN
    try:
        bugzilla_module.set_token("test_token")

        # User with editbugs who was last seen over a year ago
        stale_date = "2022-01-01T00:00:00Z"
        responses.add(
            responses.GET,
            "https://bugzilla.mozilla.org/rest/user",
            json={
                "users": [
                    {
                        "name": "stale@example.com",
                        "groups": [
                            {"id": 9, "name": "editbugs"},
                            {"id": 69, "name": "everyone"},
                        ],
                        "last_seen_date": stale_date,
                    }
                ],
                "faults": [],
            },
            status=200,
        )

        results = _check_users_batch(["stale@example.com"])
        assert results["stale@example.com"] is False

    finally:
        BugzillaBase.TOKEN = old_token


@responses.activate
def test_moco_without_recent_activity_is_trusted():
    """Test that MOCO users are trusted regardless of activity date."""
    from libmozdata.bugzilla import BugzillaBase

    old_token = BugzillaBase.TOKEN
    try:
        bugzilla_module.set_token("test_token")

        # MOCO user with stale activity is still trusted
        stale_date = "2022-01-01T00:00:00Z"
        responses.add(
            responses.GET,
            "https://bugzilla.mozilla.org/rest/user",
            json={
                "users": [
                    {
                        "name": "moco@mozilla.com",
                        "groups": [
                            {"id": 42, "name": "mozilla-corporation"},
                            {"id": 69, "name": "everyone"},
                        ],
                        "last_seen_date": stale_date,
                    }
                ],
                "faults": [],
            },
            status=200,
        )

        results = _check_users_batch(["moco@mozilla.com"])
        assert results["moco@mozilla.com"] is True

    finally:
        BugzillaBase.TOKEN = old_token


@responses.activate
def test_editbugs_without_last_seen_is_untrusted():
    """Test that editbugs users without last_seen_date are not trusted."""
    from libmozdata.bugzilla import BugzillaBase

    old_token = BugzillaBase.TOKEN
    try:
        bugzilla_module.set_token("test_token")

        # User with editbugs but no last_seen_date field
        responses.add(
            responses.GET,
            "https://bugzilla.mozilla.org/rest/user",
            json={
                "users": [
                    {
                        "name": "no_activity@example.com",
                        "groups": [
                            {"id": 9, "name": "editbugs"},
                            {"id": 69, "name": "everyone"},
                        ],
                        # No last_seen_date field
                    }
                ],
                "faults": [],
            },
            status=200,
        )

        results = _check_users_batch(["no_activity@example.com"])
        assert results["no_activity@example.com"] is False

    finally:
        BugzillaBase.TOKEN = old_token
