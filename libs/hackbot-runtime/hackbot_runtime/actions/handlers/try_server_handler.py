"""Apply-side try-server action: push an already-built patch series to Lando."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from email.utils import format_datetime
from functools import lru_cache
from typing import Any

from lando_client import LandoClient, encode_patch

from hackbot_runtime.actions.handlers.base import ActionResult, ApplyContext

log = logging.getLogger(__name__)

_TRY_PUSH_ARTIFACT_KEY = "changes/try_push.json"

# Which Try repository the push lands on, and the Treeherder repo it shows up
# under. Firefox's plain "try"; a Thunderbird agent would need
# "try-comm-central" here (and a matching Lando permission).
_TRY_REPO_NAME = "try"

_TRY_CONFIG_FILENAME = "try_task_config.json"

_DEFAULT_TITLE = "Hackbot try push"
_COMMIT_BODY = "Pushed via hackbot."

# Author of the generated try_task_config.json commit. Cosmetic (Lando attributes
# the push to the authenticated user), but it keeps the commit identifiable.
_AUTHOR = "Hackbot <hackbot@mozilla.tld>"


# One `git format-patch` email adding `try_task_config.json`. The diffstat block
# a real format-patch puts after "---" is omitted: Lando skips everything
# between the commit message and the first "diff " line, and `git apply` derives
# the stat from the diff itself.
_PATCH_TEMPLATE = """\
From 0000000000000000000000000000000000000000 Mon Sep 17 00:00:00 2001
From: {author}
Date: {date}
Subject: [PATCH] {title}

{body}
---
diff --git a/{filename} b/{filename}
new file mode 100644
--- /dev/null
+++ b/{filename}
@@ -0,0 +1,{line_count} @@
{added_lines}
--
"""


@lru_cache(maxsize=1)
def _client() -> LandoClient:
    return LandoClient()


def try_task_config(
    tasks: list[str] | None = None,
    *,
    auto: bool = False,
    test_paths: dict[str, list[str]] | None = None,
) -> dict:
    """The ``try_task_config.json`` contents for one push.

    Two shapes, matching the two `mach try` selectors this mirrors:

    * ``auto`` — CI decides the tasks from the changed files, i.e. what
      ``mach try auto`` pushes.
    * ``tasks`` — the given labels run verbatim, with
      ``optimize_target_tasks`` off so the selection is honoured (their
      dependencies can still be optimised away). ``TRY_SELECTOR`` is reported as
      ``fuzzy``, the selector whose pushes this is shaped like: it reaches the
      tasks as an environment variable, so an invented value would be one no
      in-tree consumer has ever seen.

    ``test_paths`` (``{suite: [paths]}``, resolved agent-side) narrows either
    shape to those tests via ``MOZHARNESS_TEST_PATHS``, as ``mach try``'s path
    argument does. It is a modifier, never a selection of its own: mozharness
    only consults it for a suite that is already scheduled.
    """
    if auto and tasks:
        raise ValueError("A try push selects tasks either automatically or by label")
    if not auto and not tasks:
        raise ValueError("A try push needs `auto` or a non-empty `tasks`")

    if auto:
        # Copied from TRY_AUTO_PARAMETERS in tools/tryselect/selectors/auto.py.
        parameters = {
            "filters": ["try_auto"],
            "optimize_strategies": (
                "gecko_taskgraph.optimize:tryselect."
                "bugbug_reduced_manifests_config_selection_medium"
            ),
            "optimize_target_tasks": True,
            "test_manifest_loader": "bugbug",
            "try_mode": "try_auto",
            # Empty upstream too: auto sets no TRY_SELECTOR, because it builds its
            # config directly instead of going through generate_try_task_config.
            "try_task_config": {},
        }
    else:
        parameters = {
            "optimize_target_tasks": False,
            "try_task_config": {
                "env": {"TRY_SELECTOR": "fuzzy"},
                "tasks": sorted(set(tasks or [])),
            },
        }

    if test_paths:
        env = parameters["try_task_config"].setdefault("env", {})
        env["MOZHARNESS_TEST_PATHS"] = json.dumps(
            {suite: sorted(set(paths)) for suite, paths in test_paths.items()},
            sort_keys=True,
        )

    return {"version": 2, "parameters": parameters}


def try_task_config_patch(
    tasks: list[str] | None = None,
    title: str | None = None,
    now: datetime | None = None,
    *,
    auto: bool = False,
    test_paths: dict[str, list[str]] | None = None,
) -> bytes:
    """A ``git format-patch`` email whose one commit adds ``try_task_config.json``.

    Appended to the agent's own patches as the tip commit of the try push, the
    way ``mach try`` commits the same file on top of the working tree.
    """
    content = (
        json.dumps(
            try_task_config(tasks, auto=auto, test_paths=test_paths),
            indent=4,
            sort_keys=True,
        )
        + "\n"
    )
    added_lines = content.splitlines()
    patch = _PATCH_TEMPLATE.format(
        author=_AUTHOR,
        date=format_datetime(now or datetime.now(timezone.utc)),
        title=_commit_title(title),
        body=_COMMIT_BODY,
        filename=_TRY_CONFIG_FILENAME,
        line_count=len(added_lines),
        added_lines="\n".join(f"+{line}" for line in added_lines),
    )
    return patch.encode()


def _commit_title(title: str | None) -> str:
    """A single-line commit subject for the try commit.

    The agent's title is free text, so collapse it to one line: a newline in it
    would end the ``Subject`` header early and push the rest of the title into
    the patch body (or, worse, be read as another header).
    """
    single_line = " ".join((title or "").split())
    return single_line or _DEFAULT_TITLE


class PushHandler:
    """Applies ``try_server.push``: the run's commits become a try push."""

    async def apply(self, params: dict[str, Any], ctx: ApplyContext) -> ActionResult:
        tasks = params.get("tasks") or []
        auto = bool(params.get("auto"))
        test_paths = params.get("test_paths") or None
        if not tasks and not auto:
            return ActionResult.failed(
                "A try push needs a selection: either `auto` or task labels"
            )

        try:
            raw = await ctx.download_artifact(_TRY_PUSH_ARTIFACT_KEY)
            submission = json.loads(raw)
        except Exception as exc:
            log.exception("Failed to load try push artifact for run %s", ctx.run_id)
            return ActionResult.failed(f"No try push artifact for this run: {exc}")

        try:
            client = _client()
            job_id = await client.submit_try_patches(
                [
                    *submission["patches"],
                    encode_patch(
                        try_task_config_patch(
                            tasks,
                            params.get("title"),
                            auto=auto,
                            test_paths=test_paths,
                        )
                    ),
                ],
                submission["base_commit"],
                base_commit_vcs=submission["base_commit_vcs"],
                patch_format=submission["patch_format"],
                repo_name=_TRY_REPO_NAME,
            )
        except Exception as exc:
            log.exception("Failed to push run %s to try", ctx.run_id)
            return ActionResult.failed(str(exc))

        return ActionResult.ok(
            {
                "job_id": job_id,
                # `url` is the field a `{{actions.<ref>.url}}` placeholder reads,
                # so it is the one a human would want from a try push.
                "url": client.treeherder_url(job_id, _TRY_REPO_NAME),
                "lando_url": client.job_url(job_id),
                # What was actually selected, so the row records it without a
                # reader having to re-read the recorded params.
                "selection": "auto" if auto else "tasks",
                "tasks": sorted(set(tasks)),
                **({"test_paths": test_paths} if test_paths else {}),
            }
        )
