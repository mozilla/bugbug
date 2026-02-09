# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Bugzilla integration for code review."""

import logging
import os
from datetime import datetime, timezone
from functools import cached_property

from libmozdata.bugzilla import Bugzilla, BugzillaBase

logger = logging.getLogger(__name__)

EDITBUGS_GROUP_ID = 9
TRUST_BEFORE_DATE = datetime(2022, 1, 1, tzinfo=timezone.utc)

REDACTED_TITLE = "[Unvalidated bug title redacted for security]"
REDACTED_REPORTER = "- **Reporter**: [Redacted]"
REDACTED_ASSIGNEE = "- **Assignee**: [Redacted]"

COLLAPSED_COMMENT_TAGS = {
    "abuse-reviewed",
    "abusive-reviewed",
    "admin-reviewed",
    "obsolete",
    "spam",
    "me-too",
    "typo",
    "metoo",
    "advocacy",
    "off-topic",
    "offtopic",
    "abuse",
    "abusive",
    "mozreview-request",
    "about-support",
    "duplicate",
    "empty",
    "collapsed",
    "admin",
    "hide",
    "nsfw",
}

BugzillaBase.TOKEN = os.getenv("BUGZILLA_TOKEN")


def _check_users_batch(emails: list[str]) -> dict[str, bool]:
    """Check multiple users at once using libmozdata.

    Args:
        emails: List of email addresses to check

    Returns:
        Dictionary mapping email to trusted status based on editbugs group membership.
        All Mozilla Corporation members are inherently in editbugs group.

    Raises:
        ValueError: If Bugzilla token is not available
        Various exceptions from API calls (network errors, etc.)
    """
    from libmozdata.bugzilla import BugzillaUser

    results: dict[str, bool] = {}

    if not emails:
        return results

    from libmozdata.bugzilla import BugzillaBase

    if not BugzillaBase.TOKEN:
        raise ValueError(
            "Bugzilla token required for trusted user check. "
            "Set BUGZILLA_TOKEN environment variable."
        )

    def user_handler(user, data):
        email = user.get("name", "").lower()
        if not email:
            return

        groups = user.get("groups", [])
        group_ids = {g.get("id") for g in groups}

        # Trusted if user has editbugs (all MOCO members are in editbugs)
        is_trusted = EDITBUGS_GROUP_ID in group_ids
        data[email] = is_trusted

    def fault_user_handler(user, data):
        # Handle users not found - mark as untrusted
        pass

    user_data: dict[str, bool] = {}
    BugzillaUser(
        user_names=emails,
        include_fields=["name", "groups"],
        user_handler=user_handler,
        fault_user_handler=fault_user_handler,
        user_data=user_data,
    ).wait()

    # Add results, defaulting to False for users not found
    for email in emails:
        results[email] = user_data.get(email.lower(), False)

    return results


def _is_before_trust_cutoff(timestamp_str: str) -> bool:
    """Check if a timestamp is before the trust cutoff date (2022-01-01).

    Comments before this date are automatically trusted as prompt injection
    was not a concern at that time.

    Args:
        timestamp_str: ISO format timestamp string (e.g., "2021-12-31T23:59:59Z")

    Returns:
        True if timestamp is before 2022-01-01, False otherwise
    """
    try:
        timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        return timestamp < TRUST_BEFORE_DATE
    except (ValueError, AttributeError):
        return False


def _sanitize_timeline_items(
    comments: list[dict], history: list[dict], cache: dict[str, bool]
) -> tuple[list[dict], list[dict], int, int]:
    """Sanitize timeline items by filtering untrusted content.

    Walks timeline backwards to find last trusted COMMENT (from MOCO user).
    All content before the last MOCO comment is included (validated by MOCO).
    Content after the last MOCO comment is filtered if not from MOCO.
    Only comments (not metadata changes) imply content review.

    Args:
        comments: List of comment dictionaries
        history: List of history event dictionaries
        cache: Cache of email -> trusted status lookups

    Returns:
        Tuple of (sanitized_comments, sanitized_history, filtered_comments_count, filtered_history_count)

    Security: Fail-closed - untrusted content after last MOCO comment is replaced with placeholder.
    """
    all_emails = set()
    for comment in comments:
        email = comment.get("author", "")
        if email:
            all_emails.add(email)
    for event in history:
        email = event.get("who", "")
        if email:
            all_emails.add(email)

    uncached_emails = [email for email in all_emails if email not in cache]
    if uncached_emails:
        batch_results = _check_users_batch(uncached_emails)
        cache.update(batch_results)

    # Find last trusted comment time
    last_trusted_time = None
    for comment in reversed(comments):
        tags = comment.get("tags", [])
        if any(tag in COLLAPSED_COMMENT_TAGS for tag in tags):
            continue
        email = comment.get("author", "")
        comment_time = comment["time"]
        is_trusted = cache.get(email, False) or _is_before_trust_cutoff(comment_time)
        if is_trusted:
            last_trusted_time = comment_time
            break

    filtered_comments_count = 0
    filtered_history_count = 0
    sanitized_comments = []
    sanitized_history = []

    for comment in comments:
        tags = comment.get("tags", [])
        if any(tag in COLLAPSED_COMMENT_TAGS for tag in tags):
            continue

        email = comment.get("author", "")
        comment_time = comment["time"]
        is_trusted = cache.get(email, False) or _is_before_trust_cutoff(comment_time)
        should_filter = not is_trusted and (
            last_trusted_time is None or comment_time > last_trusted_time
        )

        if should_filter:
            filtered_comments_count += 1
            comment_copy = comment.copy()
            comment_copy["text"] = "[Content from untrusted user removed for security]"
            sanitized_comments.append(comment_copy)
        else:
            sanitized_comments.append(comment)

    for event in history:
        email = event.get("who", "")
        event_time = event["when"]
        is_trusted = cache.get(email, False) or _is_before_trust_cutoff(event_time)
        should_filter = not is_trusted and (
            last_trusted_time is None or event_time > last_trusted_time
        )

        if should_filter:
            filtered_history_count += 1
            event_copy = event.copy()
            sanitized_changes = []
            for change in event_copy["changes"]:
                sanitized_changes.append(
                    {
                        "field_name": change["field_name"],
                        "removed": "[Filtered]",
                        "added": "[Filtered]",
                    }
                )
            event_copy["changes"] = sanitized_changes
            sanitized_history.append(event_copy)
        else:
            sanitized_history.append(event)

    return (
        sanitized_comments,
        sanitized_history,
        filtered_comments_count,
        filtered_history_count,
    )


def create_bug_timeline(comments: list[dict], history: list[dict]) -> list[str]:
    """Create a unified timeline from bug history and comments."""
    events = []

    ignored_fields = {"cc", "flagtypes.name"}

    # Add history events
    for event in history:
        changes = [
            change
            for change in event["changes"]
            if change["field_name"] not in ignored_fields
        ]
        if not changes:
            continue

        events.append(
            {
                "time": event["when"],
                "type": "change",
                "who": event["who"],
                "details": changes,
            }
        )

    # Add comments
    for comment in comments:
        events.append(
            {
                "time": comment["time"],
                "type": "comment",
                "who": comment["author"],
                "id": comment["id"],
                "count": comment["count"],
                "text": comment["text"],
            }
        )

    # Sort by timestamp
    events.sort(key=lambda x: (x["time"], x["type"] == "change"))

    # Format timeline
    timeline = []

    last_event = None
    for event in events:
        date = event["time"][:10]
        time = event["time"][11:19]

        if last_event and last_event["time"] != event["time"]:
            timeline.append("---\n")

        last_event = event

        if event["type"] == "comment":
            timeline.append(
                f"**{date} {time}** - Comment #{event['count']} by {event['who']}"
            )
            timeline.append(f"{event['text']}\n")
        else:
            timeline.append(f"**{date} {time}** - Changes by {event['who']}")
            for change in event["details"]:
                field = change.get("field_name", "unknown")
                old = change.get("removed", "")
                new = change.get("added", "")
                if old or new:
                    timeline.append(f"  - {field}: '{old}' → '{new}'")
            timeline.append("")

    return timeline


def bug_to_markdown(bug: "Bug") -> str:
    """Convert a Bug object to markdown representation.

    Uses the bug's properties directly - sanitization is handled by the Bug
    subclass (SanitizedBug) through property overrides.
    """
    md_lines = []

    md_lines.append(f"# Bug {bug.id or 'Unknown'} - {bug.summary}")
    md_lines.append("")

    md_lines.append("## Basic Information")
    md_lines.append(f"- **Status**: {bug.status}")
    md_lines.append(f"- **Severity**: {bug.severity}")
    md_lines.append(f"- **Product**: {bug.product}")
    md_lines.append(f"- **Component**: {bug.component}")
    md_lines.append(f"- **Version**: {bug.version}")
    md_lines.append(f"- **Platform**: {bug.platform}")
    md_lines.append(f"- **OS**: {bug.op_sys}")
    md_lines.append(f"- **Created**: {bug.creation_time}")
    md_lines.append(f"- **Last Updated**: {bug.last_change_time}")

    if bug.url:
        md_lines.append(f"- **Related URL**: {bug.url}")

    if bug.keywords:
        md_lines.append(f"- **Keywords**: {', '.join(bug.keywords)}")

    md_lines.append("")

    md_lines.append("## People Involved")

    if bug.reporter_display:
        md_lines.append(bug.reporter_display)

    if bug.assignee_display:
        md_lines.append(bug.assignee_display)

    cc_count = len(bug.cc)
    if cc_count > 0:
        md_lines.append(f"- **CC Count**: {cc_count} people")

    md_lines.append("")

    relationships = []
    if bug.blocks:
        relationships.append(f"**Blocks**: {', '.join(map(str, bug.blocks))}")
    if bug.depends_on:
        relationships.append(f"**Depends on**: {', '.join(map(str, bug.depends_on))}")
    if bug.regressed_by:
        relationships.append(
            f"**Regressed by**: {', '.join(map(str, bug.regressed_by))}"
        )
    if bug.duplicates:
        relationships.append(f"**Duplicates**: {', '.join(map(str, bug.duplicates))}")
    if bug.see_also:
        relationships.append(f"**See also**: {', '.join(bug.see_also)}")

    if relationships:
        md_lines.append("## Bug Relationships")
        for rel in relationships:
            md_lines.append(f"- {rel}")
        md_lines.append("")

    timeline = create_bug_timeline(bug.timeline_comments, bug.timeline_history)
    if timeline:
        md_lines.append("## Bug Timeline")
        md_lines.append("")
        md_lines.extend(timeline)

    return "\n".join(md_lines)


class Bug:
    """Represents a Bugzilla bug from bugzilla.mozilla.org."""

    def __init__(self, data: dict):
        self._metadata = data

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )
        value = self._metadata.get(name)
        if value is None and name in (
            "keywords",
            "cc",
            "blocks",
            "depends_on",
            "regressed_by",
            "duplicates",
            "see_also",
        ):
            return []
        return value

    @staticmethod
    def get(bug_id: int) -> "Bug":
        bugs: list[dict] = []
        Bugzilla(
            bug_id,
            include_fields=["_default", "comments", "history"],
            bughandler=lambda bug, data: data.append(bug),
            bugdata=bugs,
        ).get_data().wait()

        if not bugs:
            raise ValueError(f"Bug {bug_id} not found")

        bug_data = bugs[0]
        assert bug_data["id"] == bug_id

        return SanitizedBug(bug_data)

    @property
    def summary(self) -> str:
        return self._metadata.get("summary", "No summary")

    @property
    def creator_detail(self) -> dict:
        return self._metadata.get("creator_detail", {})

    @property
    def assignee_detail(self) -> dict:
        return self._metadata.get("assigned_to_detail", {})

    @property
    def reporter_display(self) -> str | None:
        detail = self.creator_detail
        if not detail:
            return None
        name = detail.get(
            "real_name", detail.get("nick", detail.get("email", "Unknown"))
        )
        email = detail.get("email", "No email")
        return f"- **Reporter**: {name} ({email})"

    @property
    def assignee_display(self) -> str | None:
        detail = self.assignee_detail
        if not detail:
            return None
        name = detail.get(
            "real_name", detail.get("nick", detail.get("email", "Unknown"))
        )
        email = detail.get("email", "No email")
        return f"- **Assignee**: {name} ({email})"

    @property
    def timeline_comments(self) -> list[dict]:
        return self._metadata.get("comments", [])

    @property
    def timeline_history(self) -> list[dict]:
        return self._metadata.get("history", [])

    def to_md(self) -> str:
        """Return a markdown representation of the bug."""
        return bug_to_markdown(self)


class SanitizedBug(Bug):
    """A Bug with untrusted content redacted based on trust policy."""

    @cached_property
    def _is_trusted_cache(self) -> dict[str, bool]:
        all_emails = set()
        for comment in self._metadata.get("comments", []):
            tags = comment.get("tags", [])
            if any(tag in COLLAPSED_COMMENT_TAGS for tag in tags):
                continue
            email = comment.get("author", "")
            if email:
                all_emails.add(email)
        for event in self._metadata.get("history", []):
            email = event.get("who", "")
            if email:
                all_emails.add(email)

        if not all_emails:
            return {}

        return _check_users_batch(list(all_emails))

    @cached_property
    def _has_trusted_comment(self) -> bool:
        for comment in self._metadata.get("comments", []):
            tags = comment.get("tags", [])
            if any(tag in COLLAPSED_COMMENT_TAGS for tag in tags):
                continue
            if self._is_trusted_cache.get(
                comment.get("author", ""), False
            ) or _is_before_trust_cutoff(comment.get("time", "")):
                return True
        return False

    @cached_property
    def _sanitized_timeline(self) -> tuple[list[dict], list[dict], int, int]:
        return _sanitize_timeline_items(
            self._metadata.get("comments", []),
            self._metadata.get("history", []),
            dict(self._is_trusted_cache),
        )

    @property
    def summary(self) -> str:
        if not self._has_trusted_comment:
            return REDACTED_TITLE
        return super().summary

    @property
    def reporter_display(self) -> str | None:
        if not self.creator_detail:
            return None
        if not self._has_trusted_comment:
            return REDACTED_REPORTER
        return super().reporter_display

    @property
    def assignee_display(self) -> str | None:
        if not self.assignee_detail:
            return None
        if not self._has_trusted_comment:
            return REDACTED_ASSIGNEE
        return super().assignee_display

    @property
    def timeline_comments(self) -> list[dict]:
        return self._sanitized_timeline[0]

    @property
    def timeline_history(self) -> list[dict]:
        return self._sanitized_timeline[1]
