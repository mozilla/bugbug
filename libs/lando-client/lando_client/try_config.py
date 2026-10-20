# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Try task selection and config commits shared by agents and action handlers."""

import json
from datetime import datetime, timezone
from email.utils import format_datetime

_TRY_CONFIG_FILENAME = "try_task_config.json"

_COMMIT_TITLE = f"Add {_TRY_CONFIG_FILENAME}"
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


def try_task_config(
    tasks: list[str] | None = None,
    *,
    auto: bool = False,
    test_paths: dict[str, list[str]] | None = None,
    rebuild: int | None = None,
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

    ``rebuild`` runs every selected task that many times, as ``mach try
    --rebuild`` does.
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

    if rebuild and rebuild > 1:
        parameters["try_task_config"]["rebuild"] = rebuild

    return {"version": 2, "parameters": parameters}


def try_task_config_patch(
    tasks: list[str] | None = None,
    now: datetime | None = None,
    *,
    auto: bool = False,
    test_paths: dict[str, list[str]] | None = None,
    rebuild: int | None = None,
) -> bytes:
    """A ``git format-patch`` email whose one commit adds ``try_task_config.json``.

    Appended to the agent's own patches as the tip commit of the try push, the
    way ``mach try`` commits the same file on top of the working tree.
    """
    content = (
        json.dumps(
            try_task_config(tasks, auto=auto, test_paths=test_paths, rebuild=rebuild),
            indent=4,
            sort_keys=True,
        )
        + "\n"
    )
    added_lines = content.splitlines()
    patch = _PATCH_TEMPLATE.format(
        author=_AUTHOR,
        date=format_datetime(now or datetime.now(timezone.utc)),
        title=_COMMIT_TITLE,
        body=_COMMIT_BODY,
        filename=_TRY_CONFIG_FILENAME,
        line_count=len(added_lines),
        added_lines="\n".join(f"+{line}" for line in added_lines),
    )
    return patch.encode()
