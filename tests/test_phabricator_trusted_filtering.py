"""Test Phabricator trusted user filtering functionality."""

from unittest.mock import MagicMock

from bugbug.tools.core.platforms.phabricator import (
    PhabricatorGeneralComment,
    PhabricatorInlineComment,
    _sanitize_comments,
)


def test_phabricator_filtering_untrusted_before_last_trusted():
    """Test that untrusted comments before last trusted activity are included.

    Logic: Walk backwards to find last trusted activity. Everything before it
    is included (validated by trusted user). Everything after is filtered.
    """
    # Create mock comments with author PHIDs
    comments = []

    # Untrusted comment (should be kept - before last trusted)
    untrusted_comment_1 = MagicMock(spec=PhabricatorGeneralComment)
    untrusted_comment_1.author_phid = "PHID-USER-untrusted1"
    untrusted_comment_1.date_created = 1000
    untrusted_comment_1.content = "Untrusted comment before last trusted"
    comments.append(untrusted_comment_1)

    # Trusted comment (last trusted)
    trusted_comment = MagicMock(spec=PhabricatorGeneralComment)
    trusted_comment.author_phid = "PHID-USER-trusted"
    trusted_comment.date_created = 2000
    trusted_comment.content = "Last trusted comment"
    comments.append(trusted_comment)

    # Untrusted comment (should be filtered - after last trusted)
    untrusted_comment_2 = MagicMock(spec=PhabricatorGeneralComment)
    untrusted_comment_2.author_phid = "PHID-USER-untrusted2"
    untrusted_comment_2.date_created = 3000
    untrusted_comment_2.content = "Untrusted comment after last trusted"
    comments.append(untrusted_comment_2)

    # Mock users_info (trust status)
    users_info = {
        "PHID-USER-untrusted1": {
            "is_trusted": False,
            "email": "untrusted1@example.com",
            "real_name": "",
        },
        "PHID-USER-trusted": {
            "is_trusted": True,
            "email": "trusted@mozilla.com",
            "real_name": "",
        },
        "PHID-USER-untrusted2": {
            "is_trusted": False,
            "email": "untrusted2@example.com",
            "real_name": "",
        },
    }

    # Apply the filtering logic using sanitization function
    all_comments = sorted(comments, key=lambda c: c.date_created)
    sanitized_comments, filtered_count = _sanitize_comments(all_comments, users_info)

    # Assertions
    assert filtered_count == 1
    assert sanitized_comments[0].content == "Untrusted comment before last trusted"
    assert sanitized_comments[1].content == "Last trusted comment"
    assert (
        sanitized_comments[2].content
        == "[Content from untrusted user removed for security]"
    )


def test_phabricator_filtering_no_trusted_users():
    """Test that all untrusted comments are filtered when there's no trusted activity."""
    # Create mock comments
    comments = []

    untrusted_comment_1 = MagicMock(spec=PhabricatorGeneralComment)
    untrusted_comment_1.author_phid = "PHID-USER-untrusted1"
    untrusted_comment_1.date_created = 1000
    untrusted_comment_1.content = "First untrusted comment"
    comments.append(untrusted_comment_1)

    untrusted_comment_2 = MagicMock(spec=PhabricatorGeneralComment)
    untrusted_comment_2.author_phid = "PHID-USER-untrusted2"
    untrusted_comment_2.date_created = 2000
    untrusted_comment_2.content = "Second untrusted comment"
    comments.append(untrusted_comment_2)

    # Mock users_info (all untrusted)
    users_info = {
        "PHID-USER-untrusted1": {
            "is_trusted": False,
            "email": "untrusted1@example.com",
            "real_name": "",
        },
        "PHID-USER-untrusted2": {
            "is_trusted": False,
            "email": "untrusted2@example.com",
            "real_name": "",
        },
    }

    # Apply the filtering logic using sanitization function
    all_comments = sorted(comments, key=lambda c: c.date_created)
    sanitized_comments, filtered_count = _sanitize_comments(all_comments, users_info)

    # Assertions
    assert filtered_count == 2
    assert (
        sanitized_comments[0].content
        == "[Content from untrusted user removed for security]"
    )
    assert (
        sanitized_comments[1].content
        == "[Content from untrusted user removed for security]"
    )


def test_phabricator_filtering_inline_comments():
    """Test filtering logic with inline comments."""
    # Create mock inline comments
    comments = []

    # Untrusted inline comment (should be kept - before last trusted)
    untrusted_inline = MagicMock(spec=PhabricatorInlineComment)
    untrusted_inline.author_phid = "PHID-USER-untrusted"
    untrusted_inline.date_created = 1000
    untrusted_inline.content = "Untrusted inline comment"
    untrusted_inline.filename = "test.py"
    untrusted_inline.start_line = 10
    untrusted_inline.line_length = 1
    untrusted_inline.is_done = False
    untrusted_inline.is_generated = False
    comments.append(untrusted_inline)

    # Trusted comment (last trusted)
    trusted_comment = MagicMock(spec=PhabricatorGeneralComment)
    trusted_comment.author_phid = "PHID-USER-trusted"
    trusted_comment.date_created = 2000
    trusted_comment.content = "Trusted review comment"
    comments.append(trusted_comment)

    # Mock users_info
    users_info = {
        "PHID-USER-untrusted": {
            "is_trusted": False,
            "email": "untrusted@example.com",
            "real_name": "",
        },
        "PHID-USER-trusted": {
            "is_trusted": True,
            "email": "trusted@mozilla.com",
            "real_name": "",
        },
    }

    # Apply the filtering logic using sanitization function
    all_comments = sorted(comments, key=lambda c: c.date_created)
    sanitized_comments, filtered_count = _sanitize_comments(all_comments, users_info)

    # Assertions
    assert filtered_count == 0
    assert (
        sanitized_comments[0].content == "Untrusted inline comment"
    )  # Kept because before trusted
    assert sanitized_comments[1].content == "Trusted review comment"
