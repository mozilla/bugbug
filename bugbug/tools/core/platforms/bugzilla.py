# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Bugzilla integration for code review."""

import logging
import os
from datetime import datetime, timedelta, timezone

from libmozdata.bugzilla import Bugzilla, BugzillaBase

logger = logging.getLogger(__name__)

MOZILLA_CORP_GROUP_ID = 42
EDITBUGS_GROUP_ID = 9
EDITBUGS_CUTOFF_DAYS = 365

BugzillaBase.TOKEN = os.getenv("BUGZILLA_TOKEN")


def _check_users_batch(emails: list[str]) -> dict[str, bool]:
    """Check multiple users at once using libmozdata.

    Args:
        emails: List of email addresses to check

    Returns:
        Dictionary mapping email to trusted status based on:
        - Mozilla Corporation group membership, OR
        - editbugs group membership AND activity within last year

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

        # Check if user is in mozilla-corporation group
        is_moco = MOZILLA_CORP_GROUP_ID in group_ids

        # Check if user has editbugs and has been seen recently
        has_editbugs = EDITBUGS_GROUP_ID in group_ids
        last_seen_date = user.get("last_seen_date")
        is_recently_active = False

        if last_seen_date:
            try:
                last_seen = datetime.fromisoformat(
                    last_seen_date.replace("Z", "+00:00")
                )
                one_year_ago = datetime.now(timezone.utc) - timedelta(
                    days=EDITBUGS_CUTOFF_DAYS
                )
                is_recently_active = last_seen > one_year_ago

                if has_editbugs and not is_recently_active:
                    days_since_seen = (datetime.now(timezone.utc) - last_seen).days
                    logger.warning(
                        f"User {email} has editbugs but hasn't been seen in {days_since_seen} days"
                    )
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse last_seen_date for {email}: {e}")

        # Trusted if: MOCO employee OR (has editbugs AND active within last year)
        is_trusted = is_moco or (has_editbugs and is_recently_active)
        data[email] = is_trusted

    def fault_user_handler(user, data):
        # Handle users not found - mark as untrusted
        pass

    user_data: dict[str, bool] = {}
    BugzillaUser(
        user_names=emails,
        include_fields=["name", "groups", "last_seen_date"],
        user_handler=user_handler,
        fault_user_handler=fault_user_handler,
        user_data=user_data,
    ).wait()

    # Add results, defaulting to False for users not found
    for email in emails:
        results[email] = user_data.get(email.lower(), False)

    return results


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
        email = comment.get("author", "")
        if cache.get(email, False):
            last_trusted_time = comment["time"]
            break

    filtered_comments_count = 0
    filtered_history_count = 0
    sanitized_comments = []
    sanitized_history = []

    for comment in comments:
        tags = comment.get("tags", [])
        if any(tag in ["spam", "off-topic"] for tag in tags):
            continue

        email = comment.get("author", "")
        is_trusted = cache.get(email, False)
        should_filter = not is_trusted and (
            last_trusted_time is None or comment["time"] > last_trusted_time
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
        is_trusted = cache.get(email, False)
        should_filter = not is_trusted and (
            last_trusted_time is None or event["when"] > last_trusted_time
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


def bug_dict_to_markdown(bug):
    md_lines = []
    is_trusted_cache: dict[str, bool] = {}

    # Sanitize comments and history before processing
    sanitized_comments, sanitized_history, filtered_comments, filtered_history = (
        _sanitize_timeline_items(bug["comments"], bug["history"], is_trusted_cache)
    )

    # Header with bug ID and summary
    md_lines.append(
        f"# Bug {bug.get('id', 'Unknown')} - {bug.get('summary', 'No summary')}"
    )
    md_lines.append("")

    # Basic Information
    md_lines.append("## Basic Information")
    md_lines.append(f"- **Status**: {bug.get('status', 'Unknown')}")
    md_lines.append(f"- **Severity**: {bug.get('severity', 'Unknown')}")
    md_lines.append(f"- **Product**: {bug.get('product', 'Unknown')}")
    md_lines.append(f"- **Component**: {bug.get('component', 'Unknown')}")
    md_lines.append(f"- **Version**: {bug.get('version', 'Unknown')}")
    md_lines.append(f"- **Platform**: {bug.get('platform', 'Unknown')}")
    md_lines.append(f"- **OS**: {bug.get('op_sys', 'Unknown')}")
    md_lines.append(f"- **Created**: {bug.get('creation_time', 'Unknown')}")
    md_lines.append(f"- **Last Updated**: {bug.get('last_change_time', 'Unknown')}")

    if bug.get("url"):
        md_lines.append(f"- **Related URL**: {bug['url']}")

    if bug.get("keywords"):
        md_lines.append(f"- **Keywords**: {', '.join(bug['keywords'])}")

    md_lines.append("")

    # People Involved
    md_lines.append("## People Involved")

    creator_detail = bug.get("creator_detail", {})
    if creator_detail:
        creator_name = creator_detail.get(
            "real_name",
            creator_detail.get("nick", creator_detail.get("email", "Unknown")),
        )
        md_lines.append(
            f"- **Reporter**: {creator_name} ({creator_detail.get('email', 'No email')})"
        )

    assignee_detail = bug.get("assigned_to_detail", {})
    if assignee_detail:
        assignee_name = assignee_detail.get(
            "real_name",
            assignee_detail.get("nick", assignee_detail.get("email", "Unknown")),
        )
        md_lines.append(
            f"- **Assignee**: {assignee_name} ({assignee_detail.get('email', 'No email')})"
        )

    # CC List (summarized)
    cc_count = len(bug.get("cc", []))
    if cc_count > 0:
        md_lines.append(f"- **CC Count**: {cc_count} people")

    md_lines.append("")

    # Dependencies and Relationships
    relationships = []
    if bug.get("blocks"):
        relationships.append(f"**Blocks**: {', '.join(map(str, bug['blocks']))}")
    if bug.get("depends_on"):
        relationships.append(
            f"**Depends on**: {', '.join(map(str, bug['depends_on']))}"
        )
    if bug.get("regressed_by"):
        relationships.append(
            f"**Regressed by**: {', '.join(map(str, bug['regressed_by']))}"
        )
    if bug.get("duplicates"):
        relationships.append(
            f"**Duplicates**: {', '.join(map(str, bug['duplicates']))}"
        )
    if bug.get("see_also"):
        relationships.append(f"**See also**: {', '.join(bug['see_also'])}")

    if relationships:
        md_lines.append("## Bug Relationships")
        for rel in relationships:
            md_lines.append(f"- {rel}")
        md_lines.append("")

    # Use sanitized timeline
    timeline = create_bug_timeline(sanitized_comments, sanitized_history)
    if timeline:
        md_lines.append("## Bug Timeline")
        md_lines.append("")
        md_lines.extend(timeline)

    return "\n".join(md_lines)


class Bug:
    """Represents a Bugzilla bug from bugzilla.mozilla.org."""

    def __init__(self, data: dict):
        self._metadata = data

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

        return Bug(bug_data)

    @property
    def summary(self) -> str:
        return self._metadata["summary"]

    def to_md(self) -> str:
        """Return a markdown representation of the bug."""
        return bug_dict_to_markdown(self._metadata)
