"""Record-time bounds on the only writes this agent can make.

These are the whole safety boundary for an unattended run: hackbot-api applies
whatever reaches ``summary.json`` on a confident run, dispatching it against a
handler registry far wider than these two action types. So the hooks refuse
anything outside the design's write surface as it is recorded -- one public
comment, plus ``cf_performance_impact``, added keywords and (on a high rating) a
needinfo to the reviewer on the triaged bug --
and the refused action never lands. Pattern from frontend-triage's hooks.py.
"""

from __future__ import annotations

from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionHook, ActionsRecorder
from performance_tools.triage.stability import emittable_keywords

from .preflight import WriteMode

ENABLED_ACTION_TYPES = ["bugzilla.add_comment", "bugzilla.update_bug"]

IMPACT_VALUES = frozenset({"none", "low", "medium", "high"})
_FIELDS = frozenset({"cf_performance_impact", "keywords", "flags"})

# Reviews every high rating and takes disagreements (Bas, 2026-10-27).
REVIEWER = "bas@basschouten.com"
REVIEW_FLAGS = [{"name": "needinfo", "status": "?", "requestee": REVIEWER}]


def _once(recorder: ActionsRecorder, action_type: str) -> None:
    if any(a["type"] == action_type for a in recorder.actions):
        raise ToolError(
            f"{action_type} is already recorded; this agent records one per run"
        )


def _this_bug(params: dict, bug_id: int) -> None:
    if params.get("bug_id") != bug_id:
        raise ToolError(f"this run triages bug {bug_id}, not {params.get('bug_id')!r}")


def add_comment_hook(recorder: ActionsRecorder, bug_id: int) -> ActionHook:
    """One public, non-empty comment on the triaged bug."""

    def hook(action: dict) -> None:
        params = action.get("params") or {}
        _once(recorder, "bugzilla.add_comment")
        _this_bug(params, bug_id)
        if params.get("is_private"):
            raise ToolError("the comment must be public")
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ToolError("the comment body is empty")

    return hook


def update_bug_hook(
    recorder: ActionsRecorder, bug_id: int, mode: WriteMode
) -> ActionHook:
    """Only the impact field and added calculator keywords, and only when unrated."""
    allowed_keywords = emittable_keywords()

    def hook(action: dict) -> None:
        params = action.get("params") or {}
        if mode == "comment_only":
            raise ToolError(
                "a person already rated this bug; comment only, never the field"
            )
        _once(recorder, "bugzilla.update_bug")
        _this_bug(params, bug_id)
        changes = params.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise ToolError("no changes to record")
        if extra := set(changes) - _FIELDS:
            raise ToolError(f"this agent may not change {sorted(extra)}")
        if (
            "cf_performance_impact" in changes
            and changes["cf_performance_impact"] not in IMPACT_VALUES
        ):
            raise ToolError(f"impact must be one of {sorted(IMPACT_VALUES)}")
        if "keywords" in changes:
            kw = changes["keywords"]
            if (
                not isinstance(kw, dict)
                or set(kw) != {"add"}
                or not isinstance(kw["add"], list)
            ):
                raise ToolError(
                    'keywords must be {"add": [...]}; this agent never removes or sets'
                )
            if bad := [k for k in kw["add"] if k not in allowed_keywords]:
                raise ToolError(f"not calculator keywords: {bad}")
        if "flags" in changes:
            if changes["flags"] != REVIEW_FLAGS:
                raise ToolError(f"the only flag this agent sets is {REVIEW_FLAGS}")
            if changes.get("cf_performance_impact") != "high":
                raise ToolError("the reviewer is needinfo'd only on a high rating")

    return hook
