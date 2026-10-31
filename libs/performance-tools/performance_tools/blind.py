"""Serve a bug as it was before perf triage, for evaluation runs.

Testing the agent on bugs engineers already triaged needs the engineer's answer
hidden: nearly every such bug carries the calculator comment with its inputs.
This wraps the broker's Bugzilla client so every tool sees the target bug, and
any related bug, as of the moment triage began. Ported from perf-triage-validator's
``as_filed`` (harness/extract_run.py), which established the cutoff rule:

- the cutoff is the first ``cf_performance_impact`` change, or the first
  calculator comment if that is earlier;
- comments and attachments from the cutoff on are dropped, and so is any
  calculator comment;
- every field change from the cutoff on is undone from history, which clears the
  rating and its keywords and restores the original product and component;
- a bug filed after the cutoff does not exist yet.

Enabled only when the broker is given ``BLIND_BUG_ID``.
"""

from __future__ import annotations

import re
from typing import Any

TRIAGE_RE = re.compile(r"perf-triage/calculator|Performance Impact Calculator", re.I)

# Bugzilla history reports these as comma-separated lists of values added/removed.
_LIST_FIELDS = {
    "keywords",
    "see_also",
    "blocks",
    "depends_on",
    "cc",
    "regressed_by",
    "regressions",
    "duplicates",
    "flagtypes.name",
}
_ATTACHMENT_PATH = re.compile(r"^bug/attachment/(\d+)$")
_BUG_SUB_PATH = re.compile(r"^bug/(\d+)/(comment|attachment|history)$")


def find_cutoff(history: list[dict], comments: list[dict]) -> str | None:
    """ISO time triage began, or None when the bug was never rated."""
    cutoff = None
    for entry in history:
        if any(c["field_name"] == "cf_performance_impact" for c in entry["changes"]):
            cutoff = entry["when"]
            break
    for c in comments[1:]:
        if TRIAGE_RE.search(c["text"]):
            if cutoff is None or c["creation_time"] < cutoff:
                cutoff = c["creation_time"]
            break
    return cutoff


def _split(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def roll_back(bug: dict, history: list[dict], cutoff: str) -> dict:
    """``bug`` with every change made at or after ``cutoff`` undone."""
    bug = dict(bug)
    for entry in reversed(history):
        if entry["when"] < cutoff:
            break
        for ch in entry["changes"]:
            field = ch["field_name"]
            if field not in bug:
                continue
            if isinstance(bug[field], list) or field in _LIST_FIELDS:
                current = (
                    [str(v) for v in bug[field]]
                    if isinstance(bug[field], list)
                    else _split(bug[field])
                )
                added, removed = set(_split(ch["added"])), _split(ch["removed"])
                values = [v for v in current if v not in added] + [
                    v for v in removed if v not in current
                ]
                if isinstance(bug[field], list) and all(
                    isinstance(v, int) for v in bug[field]
                ):
                    values = [int(v) for v in values if v.isdigit()]
                bug[field] = values
            else:
                bug[field] = ch["removed"]
    return bug


def keep_comment(c: dict, cutoff: str, index: int) -> bool:
    time = c.get("creation_time") or c.get("time", "")
    if time >= cutoff:
        return False
    # Comment 0 is the report itself; only a calculator comment after it is triage.
    return index == 0 or not TRIAGE_RE.search(c.get("text", ""))


class BlindClient:
    """A bugsy client whose responses stop at the target bug's triage cutoff."""

    def __init__(self, client: Any, bug_id: int):
        self._client = client
        comments = client.request(f"bug/{bug_id}/comment")["bugs"][str(bug_id)][
            "comments"
        ]
        self.cutoff = find_cutoff(self._history(bug_id), comments)
        if self.cutoff is None:
            raise ValueError(f"bug {bug_id} was never rated; nothing to hide")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def _history(self, bug_id: int | str) -> list[dict]:
        return self._client.request(f"bug/{bug_id}/history")["bugs"][0]["history"]

    def request(self, path: str, params: dict | None = None, **kw: Any) -> Any:
        result = self._client.request(path, params=params or {}, **kw)
        if path == "bug":
            result["bugs"] = [
                self._bug(b)
                for b in result.get("bugs", [])
                if self._created(b) < self.cutoff
            ]
            return result
        if m := _ATTACHMENT_PATH.match(path):
            atts = result.get("attachments", {})
            result["attachments"] = {
                k: a
                for k, a in atts.items()
                if a.get("creation_time", "") < self.cutoff
            }
            return result
        if m := _BUG_SUB_PATH.match(path):
            kind = m.group(2)
            if kind == "history":
                for b in result.get("bugs", []):
                    b["history"] = [e for e in b["history"] if e["when"] < self.cutoff]
            elif kind == "comment":
                for entry in result.get("bugs", {}).values():
                    entry["comments"] = [
                        c
                        for i, c in enumerate(entry["comments"])
                        if keep_comment(c, self.cutoff, i)
                    ]
            else:
                bugs = result.get("bugs", {})
                result["bugs"] = {
                    k: [a for a in v if a.get("creation_time", "") < self.cutoff]
                    for k, v in bugs.items()
                }
            return result
        return result

    def _created(self, bug: dict) -> str:
        if "creation_time" in bug or "id" not in bug:
            return bug.get("creation_time", "")
        one = self._client.request(
            "bug", params={"id": bug["id"], "include_fields": "creation_time"}
        )
        return one["bugs"][0]["creation_time"]

    def _bug(self, bug: dict) -> dict:
        if "id" not in bug:
            return bug
        return roll_back(bug, self._history(bug["id"]), self.cutoff)
