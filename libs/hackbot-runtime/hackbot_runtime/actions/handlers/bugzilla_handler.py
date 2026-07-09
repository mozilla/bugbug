"""Apply-side Bugzilla actions: turns a recorded intent into a real REST call.

Pairs with the recording side in ``actions/bugzilla.py`` — one handler per
action type recorded there. Talks to Bugzilla's REST API directly (not via
``bugbug.bugzilla``/``libmozdata``, which are built around bulk, futures-based
read pipelines): a single-bug write from an event-driven handler doesn't fit
that shape, and a plain ``requests`` call is simpler to reason about and test.
"""

from __future__ import annotations

import base64
import logging
import os
from functools import lru_cache
from typing import Any

import requests

from hackbot_runtime.actions.handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)

_DEFAULT_BUGZILLA_URL = "https://bugzilla.mozilla.org"
_TIMEOUT_SECONDS = 30


@lru_cache(maxsize=1)
def _base_url() -> str:
    return os.environ.get("BUGZILLA_URL", _DEFAULT_BUGZILLA_URL).rstrip("/") + "/rest"


def _bug_url(bug_id: int) -> str:
    root = os.environ.get("BUGZILLA_URL", _DEFAULT_BUGZILLA_URL).rstrip("/")
    return f"{root}/show_bug.cgi?id={bug_id}"


def _headers() -> dict[str, str]:
    token = os.environ.get("BUGZILLA_TOKEN", "")
    if not token:
        raise RuntimeError("BUGZILLA_TOKEN is not configured")
    return {"X-Bugzilla-API-Key": token, "Content-Type": "application/json"}


def _request(method: str, path: str, json_body: dict[str, Any]) -> dict[str, Any]:
    response = requests.request(
        method,
        f"{_base_url()}/{path}",
        json=json_body,
        headers=_headers(),
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


class UpdateBugHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        bug_id = params["bug_id"]
        # Field changes and a comment both go in one PUT /bug/{id} — Bugzilla
        # applies them as a single transaction (one bugmail, one history entry).
        # Copy `changes` so we never mutate the caller's dict when folding in a
        # comment (the coalescer hands us params built from other rows).
        body = dict(params.get("changes", {}))
        if params.get("comment"):
            body["comment"] = params["comment"]
        try:
            _request("PUT", f"bug/{bug_id}", body)
        except Exception as exc:
            log.exception("Failed to update bug %s", bug_id)
            return ActionResult.failed(str(exc))
        return ActionResult.ok({"bug_id": bug_id, "url": _bug_url(bug_id)})


class AddCommentHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        bug_id = params["bug_id"]
        body = {
            "comment": {
                "body": params["text"],
                "is_private": params.get("is_private", False),
            }
        }
        try:
            _request("PUT", f"bug/{bug_id}", body)
        except Exception as exc:
            log.exception("Failed to add comment to bug %s", bug_id)
            return ActionResult.failed(str(exc))
        return ActionResult.ok({"bug_id": bug_id, "url": _bug_url(bug_id)})


class AddAttachmentHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        bug_id = params["bug_id"]
        key = ctx.artifact_key("file")
        if key is None:
            return ActionResult.failed("No 'file' attachment recorded for this action")

        try:
            content = await ctx.download_artifact(key)
        except Exception as exc:
            log.exception("Failed to download attachment artifact %s", key)
            return ActionResult.failed(str(exc))

        body: dict[str, Any] = {
            "ids": [bug_id],
            "data": base64.b64encode(content).decode("ascii"),
            "file_name": params["file_name"],
            "summary": params["summary"],
            "content_type": params["content_type"],
            "is_patch": params.get("is_patch", False),
        }
        if params.get("comment"):
            body["comment"] = params["comment"]

        try:
            data = _request("POST", f"bug/{bug_id}/attachment", body)
        except Exception as exc:
            log.exception("Failed to attach file to bug %s", bug_id)
            return ActionResult.failed(str(exc))

        attachment_ids = data.get("ids") or []
        return ActionResult.ok(
            {
                "bug_id": bug_id,
                "url": _bug_url(bug_id),
                "attachment_id": attachment_ids[0] if attachment_ids else None,
            }
        )


class CreateBugHandler:
    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        try:
            data = _request("POST", "bug", params)
        except Exception as exc:
            log.exception("Failed to create bug: %s", params.get("summary"))
            return ActionResult.failed(str(exc))

        bug_id = data.get("id")
        return ActionResult.ok(
            {"bug_id": bug_id, "url": _bug_url(bug_id) if bug_id else None}
        )


_MERGEABLE_TYPES = ("bugzilla.update_bug", "bugzilla.add_comment")


def _closest_comment(update_idxs: list[int], comment_idxs: list[int]) -> int:
    """Pick the comment nearest (in idx order) to the field updates.

    Distance is to the closest update; ties break toward the earliest comment.
    """
    return min(
        comment_idxs,
        key=lambda c: (min(abs(c - u) for u in update_idxs), c),
    )


def plan_coalesced_groups(
    actions: list[tuple[str, dict[str, Any]]],
) -> list[list[int]]:
    """Return index groups of same-bug actions to apply as one ``PUT /bug/{id}``.

    ``actions`` is every pending ``(action_type, params)`` in idx order. Field
    changes for a bug are merged together and ride with the single comment
    *closest* to them (Bugzilla takes one ``comment`` object per PUT); any other
    comments on the bug are left to apply on their own. So each returned group
    is ``[update idxs..., one comment idx]`` (update + comment) or
    ``[update idxs...]`` (changes-only). Only groups of >= 2 indices are
    returned — a lone action needs no coalescing and applies as before.
    Comment-only bugs return nothing: distinct comments stay distinct PUTs.
    """
    updates: dict[Any, list[int]] = {}
    comments: dict[Any, list[int]] = {}
    for idx, (action_type, params) in enumerate(actions):
        if action_type not in _MERGEABLE_TYPES:
            continue
        bug_id = params.get("bug_id")
        if bug_id is None:
            continue
        bucket = updates if action_type == "bugzilla.update_bug" else comments
        bucket.setdefault(bug_id, []).append(idx)

    groups: list[list[int]] = []
    for bug_id, update_idxs in updates.items():
        group = list(update_idxs)
        bug_comments = comments.get(bug_id)
        if bug_comments:
            group.append(_closest_comment(update_idxs, bug_comments))
        if len(group) >= 2:
            groups.append(sorted(group))
    return groups


def merge_resolved(entries: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """Build combined :class:`UpdateBugHandler` params from a planned group.

    ``entries`` are placeholder-resolved ``(action_type, params)`` tuples for a
    group from :func:`plan_coalesced_groups`, in idx order: all the bug's field
    changes merged (later writes win) plus at most one comment.
    """
    bug_id = entries[0][1]["bug_id"]
    changes: dict[str, Any] = {}
    comment: dict[str, Any] | None = None
    for action_type, params in entries:
        if action_type == "bugzilla.update_bug":
            changes.update(params.get("changes", {}))
        elif action_type == "bugzilla.add_comment":
            comment = {
                "body": params["text"],
                "is_private": bool(params.get("is_private", False)),
            }

    combined: dict[str, Any] = {"bug_id": bug_id, "changes": changes}
    if comment is not None:
        combined["comment"] = comment
    return combined
