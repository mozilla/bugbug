"""Test Phabricator trusted user filtering functionality."""

import os
from unittest.mock import MagicMock, patch

import pytest

from bugbug import phabricator
from bugbug.tools.core.platforms.phabricator import (
    MOCO_GROUP_PHID,
    UNTRUSTED_CONTENT_REDACTED,
    PhabricatorGeneralComment,
    PhabricatorInlineComment,
    PhabricatorPatch,
    _get_users_info_batch,
    _sanitize_comments,
)

# Test user PHIDs (real PHIDs from Mozilla Phabricator)
PHAB_BOT_PHID = "PHID-USER-ub7ohlqtjctu4ofsjlb7"  # trusted
REVIEWBOT_PHID = "PHID-USER-cje4weq32o3xyuegalpj"  # trusted
UNTRUSTED_PHID = "PHID-USER-vlozqpnh2rrmayxo4r3i"  # untrusted


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
    untrusted_comment_1.content_redacted = False
    comments.append(untrusted_comment_1)

    # Trusted comment (last trusted)
    trusted_comment = MagicMock(spec=PhabricatorGeneralComment)
    trusted_comment.author_phid = "PHID-USER-trusted"
    trusted_comment.date_created = 2000
    trusted_comment.content = "Last trusted comment"
    trusted_comment.content_redacted = False
    comments.append(trusted_comment)

    # Untrusted comment (should be filtered - after last trusted)
    untrusted_comment_2 = MagicMock(spec=PhabricatorGeneralComment)
    untrusted_comment_2.author_phid = "PHID-USER-untrusted2"
    untrusted_comment_2.date_created = 3000
    untrusted_comment_2.content = "Untrusted comment after last trusted"
    untrusted_comment_2.content_redacted = False
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
    assert sanitized_comments[0].content_redacted is False
    assert sanitized_comments[1].content == "Last trusted comment"
    assert sanitized_comments[1].content_redacted is False
    assert sanitized_comments[2].content == UNTRUSTED_CONTENT_REDACTED
    assert sanitized_comments[2].content_redacted is True


def test_phabricator_filtering_no_trusted_users():
    """Test that all untrusted comments are filtered when there's no trusted activity."""
    # Create mock comments
    comments = []

    untrusted_comment_1 = MagicMock(spec=PhabricatorGeneralComment)
    untrusted_comment_1.author_phid = "PHID-USER-untrusted1"
    untrusted_comment_1.date_created = 1000
    untrusted_comment_1.content = "First untrusted comment"
    untrusted_comment_1.content_redacted = False
    comments.append(untrusted_comment_1)

    untrusted_comment_2 = MagicMock(spec=PhabricatorGeneralComment)
    untrusted_comment_2.author_phid = "PHID-USER-untrusted2"
    untrusted_comment_2.date_created = 2000
    untrusted_comment_2.content = "Second untrusted comment"
    untrusted_comment_2.content_redacted = False
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
    assert sanitized_comments[0].content == UNTRUSTED_CONTENT_REDACTED
    assert sanitized_comments[0].content_redacted is True
    assert sanitized_comments[1].content == UNTRUSTED_CONTENT_REDACTED
    assert sanitized_comments[1].content_redacted is True


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
    untrusted_inline.content_redacted = False
    comments.append(untrusted_inline)

    # Trusted comment (last trusted)
    trusted_comment = MagicMock(spec=PhabricatorGeneralComment)
    trusted_comment.author_phid = "PHID-USER-trusted"
    trusted_comment.date_created = 2000
    trusted_comment.content = "Trusted review comment"
    trusted_comment.content_redacted = False
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
    # Kept because before trusted
    assert sanitized_comments[0].content == "Untrusted inline comment"
    assert sanitized_comments[0].content_redacted is False
    assert sanitized_comments[1].content == "Trusted review comment"
    assert sanitized_comments[1].content_redacted is False


class TestToMdEndToEnd:
    """End-to-end tests for to_md() with mocked API responses.

    These tests exercise the full code path from PhabricatorPatch.to_md()
    through to the final markdown output, catching integration bugs that
    unit tests miss. All response are taken from real API response, modified
    for brevity.
    """

    MOCK_REVISION = {
        "id": 999999,
        "type": "DREV",
        "phid": "PHID-DREV-testrevision",
        "fields": {
            "title": "Test revision for filtering",
            "uri": "https://phabricator.services.mozilla.com/D999999",
            "authorPHID": PHAB_BOT_PHID,
            "status": {"value": "published", "name": "Closed", "closed": True},
            "diffPHID": "PHID-DIFF-testdiff",
            "diffID": "123456",
            "summary": "Test summary",
            "testPlan": "",
            "dateCreated": 1700000000,
            "dateModified": 1700000100,
            "bugzilla.bug-id": "1234567",
            "stackGraph": {"PHID-DREV-testrevision": []},
        },
        "attachments": {},
    }

    MOCK_DIFF = [
        {
            "id": 123456,
            "type": "DIFF",
            "phid": "PHID-DIFF-testdiff",
            "revisionPHID": "PHID-DREV-testrevision",
            "authorPHID": PHAB_BOT_PHID,
            "dateCreated": 1700000000,
            "dateModified": 1700000100,
            "baseRevision": "abc123",
            "refs": {"base": {"type": "base", "identifier": "abc123"}},
        }
    ]

    MOCK_TRANSACTIONS_WITH_COMMENTS = {
        "data": [
            # Comment 1: from trusted user (phab-bot)
            {
                "id": 1,
                "type": "comment",
                "authorPHID": PHAB_BOT_PHID,
                "dateCreated": 1700000010,
                "dateModified": 1700000010,
                "comments": [
                    {
                        "id": 101,
                        "phid": "PHID-XCMT-1",
                        "dateCreated": 1700000010,
                        "dateModified": 1700000010,
                        "content": {"raw": "Trusted comment from phab-bot"},
                    }
                ],
                "fields": {},
            },
            # Comment 2: from untrusted user, but before last trusted, this is
            # going to be displayed
            {
                "id": 2,
                "type": "comment",
                "authorPHID": UNTRUSTED_PHID,
                "dateCreated": 1700000020,
                "dateModified": 1700000020,
                "comments": [
                    {
                        "id": 102,
                        "phid": "PHID-XCMT-2",
                        "dateCreated": 1700000020,
                        "dateModified": 1700000020,
                        "content": {"raw": "Untrusted comment (before last trusted)"},
                    }
                ],
                "fields": {},
            },
            # Comment 3: from trusted user (reviewbot), last trusted comment
            {
                "id": 3,
                "type": "comment",
                "authorPHID": REVIEWBOT_PHID,
                "dateCreated": 1700000030,
                "dateModified": 1700000030,
                "comments": [
                    {
                        "id": 103,
                        "phid": "PHID-XCMT-3",
                        "dateCreated": 1700000030,
                        "dateModified": 1700000030,
                        "content": {"raw": "Last trusted comment from reviewbot"},
                    }
                ],
                "fields": {},
            },
            # Comment 4: from untrusted user after last trusted (should be filtered)
            {
                "id": 4,
                "type": "comment",
                "authorPHID": UNTRUSTED_PHID,
                "dateCreated": 1700000040,
                "dateModified": 1700000040,
                "comments": [
                    {
                        "id": 104,
                        "phid": "PHID-XCMT-4",
                        "dateCreated": 1700000040,
                        "dateModified": 1700000040,
                        "content": {"raw": "MALICIOUS CONTENT - should be filtered"},
                    }
                ],
                "fields": {},
            },
        ],
    }

    MOCK_USERS = {
        "data": [
            {
                "id": 8,
                "phid": PHAB_BOT_PHID,
                "fields": {
                    "username": "phab-bot",
                    "realName": "Phabricator Automation",
                },
            },
            {
                "id": 155,
                "phid": REVIEWBOT_PHID,
                "fields": {"username": "reviewbot", "realName": "Code Review Bot"},
            },
            {
                "id": 6790,
                "phid": UNTRUSTED_PHID,
                "fields": {"username": "untrusted", "realName": "Untrusted User"},
            },
        ],
    }

    MOCK_MOCO_GROUP = {
        "data": [
            {
                "phid": MOCO_GROUP_PHID,
                "fields": {"name": "bmo-mozilla-employee-confidential"},
                "attachments": {
                    "members": {
                        "members": [
                            {"phid": PHAB_BOT_PHID},
                            {"phid": REVIEWBOT_PHID},
                            # UNTRUSTED_PHID is NOT in this list
                        ],
                    },
                },
            }
        ],
    }

    def _mock_api_request(self, method, **kwargs):
        """Mock for phabricator.PHABRICATOR_API.request()"""
        if method == "transaction.search":
            return self.MOCK_TRANSACTIONS_WITH_COMMENTS
        elif method == "user.search":
            return self.MOCK_USERS
        elif method == "project.search":
            return self.MOCK_MOCO_GROUP
        raise ValueError(f"Unexpected API call: {method}")

    def test_to_md_filters_untrusted_content_after_last_trusted(self):
        """Test that to_md() correctly filters untrusted content."""
        mock_api = MagicMock()
        mock_api.request = self._mock_api_request
        mock_api.search_diffs = MagicMock(return_value=self.MOCK_DIFF)
        mock_api.load_revision = MagicMock(return_value=self.MOCK_REVISION)
        mock_api.load_raw_diff = MagicMock(
            return_value="diff --git a/test.py b/test.py\n+hello"
        )

        with patch.object(phabricator, "PHABRICATOR_API", mock_api):
            patch_obj = PhabricatorPatch(diff_id=123456)
            md_output = patch_obj.to_md()

        # Trusted content should be visible
        assert "Trusted comment from phab-bot" in md_output
        assert "Last trusted comment from reviewbot" in md_output

        # Untrusted content BEFORE last trusted should be kept with author visible
        assert "Untrusted comment (before last trusted)" in md_output

        # Untrusted content AFTER last trusted should be FILTERED
        assert "MALICIOUS CONTENT" not in md_output
        assert UNTRUSTED_CONTENT_REDACTED in md_output

        # Trusted authors should show names
        assert "Phabricator Automation (phab-bot)" in md_output
        assert "Code Review Bot (reviewbot)" in md_output

        # Untrusted author BEFORE last trusted: name visible
        assert "Untrusted User (untrusted)" in md_output

        # Untrusted author AFTER last trusted: name redacted
        assert "[Untrusted User]" in md_output

    def test_to_md_all_trusted_users(self):
        """Test output when all commenters are trusted."""
        mock_transactions = {
            "data": [
                {
                    "id": 1,
                    "type": "comment",
                    "authorPHID": PHAB_BOT_PHID,
                    "dateCreated": 1700000010,
                    "dateModified": 1700000010,
                    "comments": [
                        {
                            "id": 101,
                            "phid": "PHID-XCMT-1",
                            "dateCreated": 1700000010,
                            "dateModified": 1700000010,
                            "content": {"raw": "All trusted here"},
                        }
                    ],
                    "fields": {},
                },
            ],
        }

        def mock_request(method, **kwargs):
            if method == "transaction.search":
                return mock_transactions
            elif method == "user.search":
                return self.MOCK_USERS
            elif method == "project.search":
                return self.MOCK_MOCO_GROUP
            raise ValueError(f"Unexpected: {method}")

        mock_api = MagicMock()
        mock_api.request = mock_request
        mock_api.search_diffs = MagicMock(return_value=self.MOCK_DIFF)
        mock_api.load_revision = MagicMock(return_value=self.MOCK_REVISION)
        mock_api.load_raw_diff = MagicMock(
            return_value="diff --git a/test.py b/test.py\n+hi"
        )

        with patch.object(phabricator, "PHABRICATOR_API", mock_api):
            patch_obj = PhabricatorPatch(diff_id=123456)
            md_output = patch_obj.to_md()

        assert "All trusted here" in md_output
        assert UNTRUSTED_CONTENT_REDACTED not in md_output
        assert "Phabricator Automation (phab-bot)" in md_output


# Subsequent test rely on having an API key present, and perform testing against
# the live phabricator instance. They can be helpful to validate changes
# locally, but aren't run in CI.


@pytest.mark.skipif(
    not os.environ.get("PHABRICATOR_TOKEN"),
    reason="Requires PHABRICATOR_TOKEN for authenticated API access",
)
@pytest.mark.withoutresponses
def test_get_users_info_batch_empty():
    """Test that empty PHIDs set returns empty dict."""
    phabricator.set_api_key(
        os.environ.get(
            "PHABRICATOR_URL", "https://phabricator.services.mozilla.com/api/"
        ),
        os.environ["PHABRICATOR_TOKEN"],
    )
    result = _get_users_info_batch(set())
    assert result == {}


@pytest.mark.skipif(
    not os.environ.get("PHABRICATOR_TOKEN"),
    reason="Requires PHABRICATOR_TOKEN for authenticated API access",
)
@pytest.mark.withoutresponses
def test_phabricator_end_to_end_trusted_check():
    """END-TO-END test: Verify phab-bot and reviewbot are trusted, contacting
    the actual server.
        This doesn't run in CI but can be run locally
    """
    phabricator.set_api_key(
        os.environ.get(
            "PHABRICATOR_URL", "https://phabricator.services.mozilla.com/api/"
        ),
        os.environ["PHABRICATOR_TOKEN"],
    )

    # Search for two service account that are in the right group
    phabbot_response = phabricator.PHABRICATOR_API.request(
        "user.search",
        constraints={"query": "phab-bot"},
    )
    reviewbot_response = phabricator.PHABRICATOR_API.request(
        "user.search",
        constraints={"query": "reviewbot"},
    )

    assert len(phabbot_response.get("data", [])) > 0, "User phab-bot not found"
    assert len(reviewbot_response.get("data", [])) > 0, "User reviewbot not found"

    phabbot_phid = phabbot_response["data"][0]["phid"]
    reviewbot_phid = reviewbot_response["data"][0]["phid"]

    # Get user info using our batch function with three users, one being
    # invalid
    users_info = _get_users_info_batch({phabbot_phid, reviewbot_phid, "qweqwe"})

    assert len(users_info) == 2, f"Expected 2 users, got {len(users_info)}"

    assert phabbot_phid in users_info, f"User PHID {phabbot_phid} not in results"
    assert reviewbot_phid in users_info, f"User PHID {reviewbot_phid} not in results"

    # Both accounts must be trusted
    assert users_info[phabbot_phid]["is_trusted"] is True, (
        f"phabbot@mozilla.com must be trusted. Got is_trusted={users_info[phabbot_phid]['is_trusted']}"
    )
    assert users_info[reviewbot_phid]["is_trusted"] is True, (
        f"reviewbot must be trusted. Got is_trusted={users_info[reviewbot_phid]['is_trusted']}"
    )


@pytest.mark.skipif(
    not os.environ.get("PHABRICATOR_TOKEN"),
    reason="Requires PHABRICATOR_TOKEN for authenticated API access",
)
@pytest.mark.withoutresponses
def test_get_users_info_batch_mixed_trust():
    """Test that mixed trusted/untrusted users are correctly identified. Can
    only be run with a phab token, to validate changes locally."""
    phabricator.set_api_key(
        os.environ.get(
            "PHABRICATOR_URL", "https://phabricator.services.mozilla.com/api/"
        ),
        os.environ["PHABRICATOR_TOKEN"],
    )

    # Get PHIDs for known users -- two trusted, one not trusted
    resp = phabricator.PHABRICATOR_API.request(
        "user.search",
        constraints={"usernames": ["phab-bot", "reviewbot", "YuK"]},
    )
    phids_by_username = {u["fields"]["username"]: u["phid"] for u in resp["data"]}

    users_info = _get_users_info_batch(set(phids_by_username.values()))

    # Verify structure
    for phid, info in users_info.items():
        assert "email" in info
        assert "is_trusted" in info
        assert "real_name" in info
        assert isinstance(info["is_trusted"], bool)

    # Verify trust status
    assert users_info[phids_by_username["phab-bot"]]["is_trusted"] is True
    assert users_info[phids_by_username["reviewbot"]]["is_trusted"] is True
    assert users_info[phids_by_username["YuK"]]["is_trusted"] is False


@pytest.mark.skipif(
    not os.environ.get("PHABRICATOR_TOKEN"),
    reason="Requires PHABRICATOR_TOKEN for authenticated API access",
)
@pytest.mark.withoutresponses
def test_moco_group_phid_is_valid():
    """Test that MOCO_GROUP_PHID points to a valid project. This can only be
    run with an API key, to validate changes locally."""
    phabricator.set_api_key(
        os.environ.get(
            "PHABRICATOR_URL", "https://phabricator.services.mozilla.com/api/"
        ),
        os.environ["PHABRICATOR_TOKEN"],
    )

    resp = phabricator.PHABRICATOR_API.request(
        "project.search",
        constraints={"phids": [MOCO_GROUP_PHID]},
    )

    assert len(resp["data"]) == 1
    assert resp["data"][0]["phid"] == MOCO_GROUP_PHID
    assert "bmo-mozilla-employee-confidential" in resp["data"][0]["fields"]["name"]
