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
        try:
            _request("PUT", f"bug/{bug_id}", params["changes"])
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
