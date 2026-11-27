# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Verify shared try selection and generated commits using real git."""

import json
import subprocess

from lando_client import try_task_config, try_task_config_patch


def _git(repo, *args: str) -> str:
    """Run a git command in ``repo`` and return its stdout."""
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


# --- try_task_config ----------------------------------------------------- #


def test_repeated_config_survives_lando_signature_stripping(tmp_path):
    patch = try_task_config_patch(["test-linux-speedometer3"], rebuild=3).decode()
    lines = patch.splitlines(keepends=True)
    signature = next(
        i for i in reversed(range(len(lines))) if lines[i].startswith("--")
    )
    diff_start = next(
        i for i, line in enumerate(lines) if line.startswith("diff --git ")
    )
    diff = "".join(lines[diff_start:signature])
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "apply", "-"], input=diff, text=True, cwd=tmp_path, check=True
    )
    config = json.loads((tmp_path / "try_task_config.json").read_text())
    assert config == try_task_config(["test-linux-speedometer3"], rebuild=3)
