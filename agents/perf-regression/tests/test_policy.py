# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess

import pytest
from hackbot_agents.perf_regression.policy import patch_paths


def test_real_format_patch_paths_cannot_hide(tmp_path):
    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True
        )

    git("init")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    for path, text in [("src/x.lua", "a\n-- x\n"), ("taskcluster/ok.yml", "ok\n")]:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)
    git("add", "-A")
    git("commit", "-m", "base")
    (tmp_path / "src/x.lua").write_text("a\n")  # Removed line looks like "--- x".
    (tmp_path / "taskcluster/a b.yml").write_text("spaced\n")
    (tmp_path / "testing/raptor").mkdir(parents=True)
    (tmp_path / "testing/raptor/é.py").write_text("quoted\n")
    git("mv", "taskcluster/ok.yml", "taskcluster/moved.yml")
    git("add", "-A")
    git("commit", "-m", "candidate\n\n--- not a header")
    patch = subprocess.run(
        ["git", "-C", str(tmp_path), "format-patch", "-M", "-1", "--stdout"],
        check=True,
        capture_output=True,
    ).stdout
    assert patch_paths(patch) == {
        "src/x.lua",
        "taskcluster/a b.yml",
        "taskcluster/ok.yml",
        "taskcluster/moved.yml",
        "testing/raptor/é.py",
    }


def test_headerless_hunk_is_seen():
    patch = (
        b"diff --git a/ok.txt b/ok.txt\n--- a/ok.txt\n+++ b/ok.txt\n"
        b"@@ -1 +1 @@\n-x\n+y\n"
        b"--- a/taskcluster/t.yml\n+++ b/taskcluster/t.yml\n@@ -1 +1 @@\n-a\n+b\n"
    )
    assert patch_paths(patch) == {"ok.txt", "taskcluster/t.yml"}


@pytest.mark.parametrize(
    "patch",
    [
        b"diff --git x y\n",
        b"diff --git a/x b/y b/z b/w\n",
        b"diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,3 +1,3 @@\n-a\n+b\n",
    ],
)
def test_unparsable_patches_are_rejected(patch):
    with pytest.raises(ValueError):
        patch_paths(patch)
