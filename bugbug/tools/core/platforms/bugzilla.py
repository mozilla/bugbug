# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Bugzilla integration for code review."""

from libmozdata.bugzilla import Bugzilla


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

    timeline = create_bug_timeline(bug["comments"], bug["history"])
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
