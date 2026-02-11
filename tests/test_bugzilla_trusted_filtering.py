"""Test Bugzilla trusted user filtering functionality."""

import os

import pytest
import responses

from bugbug import bugzilla as bugzilla_module
from bugbug.tools.core.platforms.bugzilla import (
    _check_users_batch,
    _sanitize_timeline_items,
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

        # Mock trusted user response (with editbugs group)
        responses.add(
            responses.GET,
            "https://bugzilla.mozilla.org/rest/user",
            json={
                "users": [
                    {
                        "name": "trusted@mozilla.com",
                        "groups": [
                            {"id": 9, "name": "editbugs"},
                        ],
                    }
                ],
                "faults": [],
            },
        )

        result = _check_users_batch(["trusted@mozilla.com"])
        assert result["trusted@mozilla.com"] is True

    finally:
        BugzillaBase.TOKEN = old_token


def test_trusted_check_without_token():
    """Test that _check_users_batch raises error when token is not set."""
    from libmozdata.bugzilla import BugzillaBase

    old_token = BugzillaBase.TOKEN
    try:
        BugzillaBase.TOKEN = None

        with pytest.raises(ValueError, match="Bugzilla token required"):
            _check_users_batch(["test@example.com"])
    finally:
        BugzillaBase.TOKEN = old_token


def test_trusted_check_empty_email():
    """Test that empty email list returns empty results."""
    from libmozdata.bugzilla import BugzillaBase

    old_token = BugzillaBase.TOKEN
    try:
        BugzillaBase.TOKEN = "test_token"
        result = _check_users_batch([])
        assert result == {}
    finally:
        BugzillaBase.TOKEN = old_token


def test_untrusted_before_last_trusted():
    """Test filtering: all content before last trusted comment is shown."""
    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "untrusted@example.com",
            "id": 1,
            "count": 0,
            "text": "Untrusted comment",
            "tags": [],
        },
        {
            "time": "2024-01-01T11:00:00Z",
            "author": "trusted@mozilla.com",
            "id": 2,
            "count": 1,
            "text": "Trusted comment",
            "tags": [],
        },
        {
            "time": "2024-01-01T12:00:00Z",
            "author": "untrusted@example.com",
            "id": 3,
            "count": 2,
            "text": "Another untrusted",
            "tags": [],
        },
    ]

    cache = {"trusted@mozilla.com": True, "untrusted@example.com": False}
    sanitized, _, filtered_count, _ = _sanitize_timeline_items(comments, [], cache)

    assert len(sanitized) == 3
    assert sanitized[0]["text"] == "Untrusted comment"
    assert sanitized[0]["author"] == "untrusted@example.com"  # Before trusted, shown
    assert sanitized[1]["text"] == "Trusted comment"
    assert sanitized[1]["author"] == "trusted@mozilla.com"
    assert "[Content from untrusted user removed for security]" in sanitized[2]["text"]
    assert sanitized[2]["author"] == "[Redacted]"  # After trusted, filtered
    assert filtered_count == 1


def test_no_trusted_users():
    """Test filtering when no trusted users exist."""
    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "untrusted@example.com",
            "id": 1,
            "count": 0,
            "text": "Untrusted comment",
            "tags": [],
        },
        {
            "time": "2024-01-01T11:00:00Z",
            "author": "another_untrusted@example.com",
            "id": 2,
            "count": 1,
            "text": "Another untrusted",
            "tags": [],
        },
    ]

    cache = {
        "untrusted@example.com": False,
        "another_untrusted@example.com": False,
    }
    sanitized, _, filtered_count, _ = _sanitize_timeline_items(comments, [], cache)

    assert len(sanitized) == 2
    assert "[Content from untrusted user removed for security]" in sanitized[0]["text"]
    assert sanitized[0]["author"] == "[Redacted]"
    assert "[Content from untrusted user removed for security]" in sanitized[1]["text"]
    assert sanitized[1]["author"] == "[Redacted]"
    assert filtered_count == 2


def test_fail_closed_scenarios():
    """Test fail-closed behavior: when uncertain, filter content."""
    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "unknown@example.com",
            "id": 1,
            "count": 0,
            "text": "Comment from unknown user",
            "tags": [],
        },
    ]

    cache = {"unknown@example.com": False}
    sanitized, _, filtered_count, _ = _sanitize_timeline_items(comments, [], cache)

    assert len(sanitized) == 1
    assert "[Content from untrusted user removed for security]" in sanitized[0]["text"]
    assert sanitized[0]["author"] == "[Redacted]"
    assert filtered_count == 1


def test_timeline_variation_alternating():
    """Test alternating trusted/untrusted comments."""
    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "trusted@mozilla.com",
            "id": 1,
            "count": 0,
            "text": "Trusted",
            "tags": [],
        },
        {
            "time": "2024-01-01T11:00:00Z",
            "author": "untrusted@example.com",
            "id": 2,
            "count": 1,
            "text": "Untrusted",
            "tags": [],
        },
        {
            "time": "2024-01-01T12:00:00Z",
            "author": "trusted@mozilla.com",
            "id": 3,
            "count": 2,
            "text": "Trusted again",
            "tags": [],
        },
        {
            "time": "2024-01-01T13:00:00Z",
            "author": "untrusted@example.com",
            "id": 4,
            "count": 3,
            "text": "Untrusted again",
            "tags": [],
        },
    ]

    cache = {"trusted@mozilla.com": True, "untrusted@example.com": False}
    sanitized, _, filtered_count, _ = _sanitize_timeline_items(comments, [], cache)

    assert len(sanitized) == 4
    assert sanitized[0]["text"] == "Trusted"
    assert sanitized[0]["author"] == "trusted@mozilla.com"
    assert sanitized[1]["text"] == "Untrusted"
    assert sanitized[1]["author"] == "untrusted@example.com"
    assert sanitized[2]["text"] == "Trusted again"
    assert sanitized[2]["author"] == "trusted@mozilla.com"
    assert "[Content from untrusted user removed for security]" in sanitized[3]["text"]
    assert sanitized[3]["author"] == "[Redacted]"
    assert filtered_count == 1


def test_timeline_variation_trusted_first():
    """Test when only the first comment is trusted."""
    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "trusted@mozilla.com",
            "id": 1,
            "count": 0,
            "text": "Trusted",
            "tags": [],
        },
        {
            "time": "2024-01-01T11:00:00Z",
            "author": "untrusted@example.com",
            "id": 2,
            "count": 1,
            "text": "Untrusted",
            "tags": [],
        },
    ]

    cache = {"trusted@mozilla.com": True, "untrusted@example.com": False}
    sanitized, _, filtered_count, _ = _sanitize_timeline_items(comments, [], cache)

    assert len(sanitized) == 2
    assert sanitized[0]["text"] == "Trusted"
    assert sanitized[0]["author"] == "trusted@mozilla.com"
    assert "[Content from untrusted user removed for security]" in sanitized[1]["text"]
    assert sanitized[1]["author"] == "[Redacted]"
    assert filtered_count == 1


def test_timeline_variation_trusted_last():
    """Test when only the last comment is trusted."""
    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "untrusted@example.com",
            "id": 1,
            "count": 0,
            "text": "Untrusted",
            "tags": [],
        },
        {
            "time": "2024-01-01T11:00:00Z",
            "author": "trusted@mozilla.com",
            "id": 2,
            "count": 1,
            "text": "Trusted",
            "tags": [],
        },
    ]

    cache = {"trusted@mozilla.com": True, "untrusted@example.com": False}
    sanitized, _, filtered_count, _ = _sanitize_timeline_items(comments, [], cache)

    assert len(sanitized) == 2
    assert sanitized[0]["text"] == "Untrusted"
    assert sanitized[0]["author"] == "untrusted@example.com"
    assert sanitized[1]["text"] == "Trusted"
    assert sanitized[1]["author"] == "trusted@mozilla.com"
    assert filtered_count == 0


def test_timeline_variation_all_trusted():
    """Test when all comments are from trusted users."""
    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "trusted1@mozilla.com",
            "id": 1,
            "count": 0,
            "text": "Trusted 1",
            "tags": [],
        },
        {
            "time": "2024-01-01T11:00:00Z",
            "author": "trusted2@mozilla.com",
            "id": 2,
            "count": 1,
            "text": "Trusted 2",
            "tags": [],
        },
    ]

    cache = {"trusted1@mozilla.com": True, "trusted2@mozilla.com": True}
    sanitized, _, filtered_count, _ = _sanitize_timeline_items(comments, [], cache)

    assert len(sanitized) == 2
    assert sanitized[0]["text"] == "Trusted 1"
    assert sanitized[0]["author"] == "trusted1@mozilla.com"
    assert sanitized[1]["text"] == "Trusted 2"
    assert sanitized[1]["author"] == "trusted2@mozilla.com"
    assert filtered_count == 0


def test_timeline_variation_multiple_trusted_positions():
    """Test multiple trusted comments with untrusted in between."""
    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "untrusted@example.com",
            "id": 1,
            "count": 0,
            "text": "Untrusted 1",
            "tags": [],
        },
        {
            "time": "2024-01-01T11:00:00Z",
            "author": "trusted@mozilla.com",
            "id": 2,
            "count": 1,
            "text": "Trusted 1",
            "tags": [],
        },
        {
            "time": "2024-01-01T12:00:00Z",
            "author": "untrusted@example.com",
            "id": 3,
            "count": 2,
            "text": "Untrusted 2",
            "tags": [],
        },
        {
            "time": "2024-01-01T13:00:00Z",
            "author": "trusted@mozilla.com",
            "id": 4,
            "count": 3,
            "text": "Trusted 2",
            "tags": [],
        },
    ]

    cache = {"trusted@mozilla.com": True, "untrusted@example.com": False}
    sanitized, _, filtered_count, _ = _sanitize_timeline_items(comments, [], cache)

    assert len(sanitized) == 4
    assert sanitized[0]["text"] == "Untrusted 1"
    assert sanitized[0]["author"] == "untrusted@example.com"
    assert sanitized[1]["text"] == "Trusted 1"
    assert sanitized[1]["author"] == "trusted@mozilla.com"
    assert sanitized[2]["text"] == "Untrusted 2"
    assert sanitized[2]["author"] == "untrusted@example.com"
    assert sanitized[3]["text"] == "Trusted 2"
    assert sanitized[3]["author"] == "trusted@mozilla.com"
    assert filtered_count == 0


def test_trusted_history_does_not_validate():
    """Test that history changes don't count as content validation."""
    comments = [
        {
            "time": "2024-01-01T10:00:00Z",
            "author": "untrusted@example.com",
            "id": 1,
            "count": 0,
            "text": "Untrusted comment",
            "tags": [],
        }
    ]
    history = [
        {
            "when": "2024-01-01T11:00:00Z",
            "who": "trusted@mozilla.com",
            "changes": [
                {"field_name": "status", "removed": "NEW", "added": "ASSIGNED"}
            ],
        }
    ]

    cache = {"trusted@mozilla.com": True, "untrusted@example.com": False}
    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(comments, history, cache)
    )

    assert (
        "[Content from untrusted user removed for security]"
        in sanitized_comments[0]["text"]
    )
    assert sanitized_comments[0]["author"] == "[Redacted]"
    assert filtered_comments == 1
    assert sanitized_history[0]["who"] == "trusted@mozilla.com"
    assert filtered_history == 0


def test_trusted_comment_validates_before_untrusted_history_after():
    """Test that trusted comment validates earlier untrusted history but not later."""
    comments = [
        {
            "time": "2024-01-01T12:00:00Z",
            "author": "trusted@mozilla.com",
            "id": 1,
            "count": 0,
            "text": "Trusted comment",
            "tags": [],
        }
    ]
    history = [
        {
            "when": "2024-01-01T10:00:00Z",
            "who": "untrusted@example.com",
            "changes": [{"field_name": "priority", "removed": "P3", "added": "P1"}],
        },
        {
            "when": "2024-01-01T13:00:00Z",
            "who": "untrusted@example.com",
            "changes": [
                {"field_name": "status", "removed": "NEW", "added": "ASSIGNED"}
            ],
        },
    ]

    cache = {"trusted@mozilla.com": True, "untrusted@example.com": False}
    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(comments, history, cache)
    )

    assert sanitized_comments[0]["text"] == "Trusted comment"
    assert sanitized_comments[0]["author"] == "trusted@mozilla.com"
    assert filtered_comments == 0
    assert len(sanitized_history) == 2
    assert sanitized_history[0]["changes"][0]["added"] == "P1"
    assert sanitized_history[0]["who"] == "untrusted@example.com"
    assert sanitized_history[1]["changes"][0]["added"] == "[Filtered]"
    assert sanitized_history[1]["who"] == "[Redacted]"
    assert filtered_history == 1


def test_extended_trusted_user_policy():
    """Test that editbugs users are trusted regardless of activity."""
    from unittest.mock import patch

    from bugbug.tools.core.platforms.bugzilla import _check_users_batch

    mock_users_response = {
        "users": [
            {
                "name": "editbugs_user@example.com",
                "groups": [{"id": 9, "name": "editbugs"}],
            },
            {
                "name": "no_editbugs@example.com",
                "groups": [],
            },
        ]
    }

    with (
        patch("libmozdata.bugzilla.BugzillaBase.TOKEN", "test_token"),
        patch("libmozdata.bugzilla.BugzillaUser") as mock_user_class,
    ):
        mock_instance = mock_user_class.return_value

        def mock_wait():
            # Get the handler and data from the constructor call
            call_kwargs = mock_user_class.call_args[1]
            user_handler = call_kwargs.get("user_handler")
            user_data = call_kwargs.get("user_data", {})

            for user in mock_users_response["users"]:
                user_handler(user, user_data)
            return mock_instance

        mock_instance.wait = mock_wait

        result = _check_users_batch(
            ["editbugs_user@example.com", "no_editbugs@example.com"]
        )

        assert result["editbugs_user@example.com"] is True
        assert result["no_editbugs@example.com"] is False


def test_metadata_redacted_without_trusted_comment():
    """Test that bug metadata is redacted when no trusted user has commented."""
    from unittest.mock import patch

    from bugbug.tools.core.platforms.bugzilla import (
        REDACTED_ASSIGNEE,
        REDACTED_REPORTER,
        REDACTED_TITLE,
        SanitizedBug,
    )

    bug_data = {
        "id": 12345,
        "summary": "This is the bug title",
        "comments": [
            {
                "time": "2024-01-01T10:00:00Z",
                "author": "untrusted@example.com",
                "id": 1,
                "count": 0,
                "text": "Untrusted comment",
            }
        ],
        "history": [],
        "status": "NEW",
        "severity": "normal",
        "product": "Core",
        "component": "General",
        "version": "unspecified",
        "platform": "All",
        "op_sys": "All",
        "creation_time": "2024-01-01T00:00:00Z",
        "last_change_time": "2024-01-01T10:00:00Z",
        "creator_detail": {
            "real_name": "Untrusted User",
            "email": "untrusted@example.com",
        },
        "assigned_to_detail": {
            "real_name": "Assignee Name",
            "email": "assignee@example.com",
        },
    }

    # Mock the user check to make untrusted@example.com untrusted
    with patch(
        "bugbug.tools.core.platforms.bugzilla._check_users_batch",
        return_value={"untrusted@example.com": False},
    ):
        markdown = SanitizedBug(bug_data).to_md()

    # Title should be redacted
    assert REDACTED_TITLE in markdown
    assert "This is the bug title" not in markdown

    # Reporter should be redacted
    assert REDACTED_REPORTER in markdown
    assert "Untrusted User" not in markdown

    # Assignee should be redacted
    assert REDACTED_ASSIGNEE in markdown
    assert "Assignee Name" not in markdown


def test_metadata_shown_with_trusted_comment():
    """Test that bug metadata is shown when a trusted user has commented."""
    from unittest.mock import patch

    from bugbug.tools.core.platforms.bugzilla import (
        REDACTED_ASSIGNEE,
        REDACTED_REPORTER,
        REDACTED_TITLE,
        SanitizedBug,
    )

    bug_data = {
        "id": 12345,
        "summary": "This is the bug title",
        "comments": [
            {
                "time": "2024-01-01T10:00:00Z",
                "author": "untrusted@example.com",
                "id": 1,
                "count": 0,
                "text": "Untrusted comment",
            },
            {
                "time": "2024-01-01T10:01:00Z",
                "author": "trusted@mozilla.com",
                "id": 2,
                "count": 1,
                "text": "Trusted comment",
            },
        ],
        "history": [],
        "status": "NEW",
        "severity": "normal",
        "product": "Core",
        "component": "General",
        "version": "unspecified",
        "platform": "All",
        "op_sys": "All",
        "creation_time": "2024-01-01T00:00:00Z",
        "last_change_time": "2024-01-01T10:01:00Z",
        "creator_detail": {
            "real_name": "Untrusted User",
            "email": "untrusted@example.com",
        },
        "assigned_to_detail": {
            "real_name": "Assignee Name",
            "email": "assignee@example.com",
        },
    }

    # Mock the user check to make trusted@mozilla.com trusted
    with patch(
        "bugbug.tools.core.platforms.bugzilla._check_users_batch",
        return_value={"untrusted@example.com": False, "trusted@mozilla.com": True},
    ):
        markdown = SanitizedBug(bug_data).to_md()

    # Title should be shown
    assert "This is the bug title" in markdown
    assert REDACTED_TITLE not in markdown

    # Reporter should be shown
    assert "Untrusted User" in markdown
    assert REDACTED_REPORTER not in markdown

    # Assignee should be shown
    assert "Assignee Name" in markdown
    assert REDACTED_ASSIGNEE not in markdown


def test_admin_tagged_comments_completely_disregarded():
    """Test that admin-tagged comments are completely ignored in all trust logic."""
    from unittest.mock import patch

    from bugbug.tools.core.platforms.bugzilla import SanitizedBug

    bug_data = {
        "id": 12345,
        "summary": "Bug with admin comment",
        "comments": [
            {
                "time": "2024-01-01T10:00:00Z",
                "author": "untrusted@example.com",
                "id": 1,
                "count": 0,
                "text": "Untrusted comment",
                "tags": [],
            },
            {
                "time": "2024-01-01T11:00:00Z",
                "author": "admin@mozilla.com",
                "id": 2,
                "count": 1,
                "text": "Admin comment",
                "tags": ["admin"],
            },
            {
                "time": "2024-01-01T12:00:00Z",
                "author": "untrusted@example.com",
                "id": 3,
                "count": 2,
                "text": "Another untrusted",
                "tags": [],
            },
        ],
        "history": [],
        "creator_detail": {"email": "reporter@example.com"},
        "assigned_to_detail": {"email": "assignee@example.com"},
    }

    with patch(
        "bugbug.tools.core.platforms.bugzilla._check_users_batch",
        return_value={"untrusted@example.com": False},
    ):
        bug = SanitizedBug(bug_data)

        # Admin comment should not count as trusted comment
        assert not bug._has_trusted_comment

        # Admin comment should not be in timeline
        comments = bug.comments
        assert len(comments) == 2
        assert all("Admin comment" not in c["text"] for c in comments)

        # Admin comment author should not be in trust cache (wasn't even checked)
        assert "admin@mozilla.com" not in bug._is_trusted_cache


def test_pre_2022_comments_trusted():
    """Test that comments before 2022-01-01 are automatically trusted."""
    from unittest.mock import patch

    from bugbug.tools.core.platforms.bugzilla import SanitizedBug

    bug_data = {
        "id": 12345,
        "summary": "Old bug",
        "comments": [
            {
                "time": "2021-12-31T23:59:59Z",
                "author": "old_user@example.com",
                "id": 1,
                "count": 0,
                "text": "Pre-2022 comment",
                "tags": [],
            },
            {
                "time": "2024-01-01T10:00:00Z",
                "author": "new_untrusted@example.com",
                "id": 2,
                "count": 1,
                "text": "Post-2022 untrusted",
                "tags": [],
            },
        ],
        "history": [],
        "creator_detail": {"email": "reporter@example.com"},
        "assigned_to_detail": {"email": "assignee@example.com"},
    }

    with patch(
        "bugbug.tools.core.platforms.bugzilla._check_users_batch",
        return_value={
            "old_user@example.com": False,
            "new_untrusted@example.com": False,
        },
    ):
        bug = SanitizedBug(bug_data)

        # Pre-2022 comment should count as trusted for metadata
        assert bug._has_trusted_comment

        # Pre-2022 comment shown as-is, post-2022 untrusted filtered
        timeline = bug.comments
        assert timeline[0]["text"] == "Pre-2022 comment"
        assert timeline[0]["author"] == "old_user@example.com"
        assert (
            "[Content from untrusted user removed for security]" in timeline[1]["text"]
        )
        assert timeline[1]["author"] == "[Redacted]"


def test_collapsed_tags_filtered():
    """Test that all collapsed tags cause comments to be filtered."""

    # Test a few different collapsed tags
    for tag in ["spam", "abuse", "nsfw", "off-topic"]:
        comments = [
            {
                "time": "2024-01-01T10:00:00Z",
                "author": "user@example.com",
                "id": 1,
                "count": 0,
                "text": f"Comment with {tag} tag",
                "tags": [tag],
            },
            {
                "time": "2024-01-01T11:00:00Z",
                "author": "user@example.com",
                "id": 2,
                "count": 1,
                "text": "Normal comment",
                "tags": [],
            },
        ]

        from bugbug.tools.core.platforms.bugzilla import _sanitize_timeline_items

        cache = {"user@example.com": False}
        sanitized, _, _, _ = _sanitize_timeline_items(comments, [], cache)

        # Only the normal comment should be in sanitized output
        assert len(sanitized) == 1
        assert sanitized[0]["text"] != f"Comment with {tag} tag"
