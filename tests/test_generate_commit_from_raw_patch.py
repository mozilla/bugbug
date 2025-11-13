# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
from datetime import datetime, timezone

import hglib
import pytest

from bugbug import repository


@pytest.fixture
def fake_hg_repo(tmpdir):
    tmp_path = tmpdir.strpath
    local = os.path.join(tmp_path, "test_repo")
    os.makedirs(local)
    hglib.init(local)

    os.environ["USER"] = "test"
    hg = hglib.open(local)

    # Create an initial commit to serve as base
    initial_file = os.path.join(local, "initial.txt")
    with open(initial_file, "w") as f:
        f.write("initial content\n")

    hg.add(files=[bytes(initial_file, "ascii")])
    hg.commit(
        message="Initial commit",
        user="Test User <test@mozilla.org>",
        date=datetime(2019, 4, 16, tzinfo=timezone.utc),
    )

    yield hg, local

    hg.close()


def test_generate_commit_from_raw_patch_single_commit(fake_hg_repo):
    """Test that a patch with a single commit returns one commit."""
    hg, local = fake_hg_repo

    # Get the base revision
    base_rev = hg.log(limit=1)[0][1].decode("ascii")

    # Create a patch with a single commit
    patch = b"""# HG changeset patch
# User Test User <test@mozilla.org>
# Date 0 0
Single commit message

diff --git a/file1.txt b/file1.txt
new file mode 100644
--- /dev/null
+++ b/file1.txt
@@ -0,0 +1,1 @@
+line1
"""

    # Apply the patch
    commits = repository.generate_commit_from_raw_patch(local, base_rev, patch)

    # Verify we got exactly one commit
    assert len(commits) == 1
    assert commits[0].desc == "Single commit message"


def test_generate_commit_from_raw_patch_multiple_commits(fake_hg_repo):
    """Test that a patch with multiple commits returns all commits."""
    hg, local = fake_hg_repo

    # Get the base revision
    base_rev = hg.log(limit=1)[0][1].decode("ascii")

    # Create a patch with multiple commits
    patch = b"""# HG changeset patch
# User Test User <test@mozilla.org>
# Date 0 0
First commit message

diff --git a/file1.txt b/file1.txt
new file mode 100644
--- /dev/null
+++ b/file1.txt
@@ -0,0 +1,1 @@
+line1

# HG changeset patch
# User Test User <test@mozilla.org>
# Date 0 0
Second commit message

diff --git a/file2.txt b/file2.txt
new file mode 100644
--- /dev/null
+++ b/file2.txt
@@ -0,0 +1,1 @@
+line2

# HG changeset patch
# User Test User <test@mozilla.org>
# Date 0 0
Third commit message

diff --git a/file3.txt b/file3.txt
new file mode 100644
--- /dev/null
+++ b/file3.txt
@@ -0,0 +1,1 @@
+line3
"""

    # Apply the patch
    commits = repository.generate_commit_from_raw_patch(local, base_rev, patch)

    # Verify we got all three commits
    assert len(commits) == 3
    assert commits[0].desc == "First commit message"
    assert commits[1].desc == "Second commit message"
    assert commits[2].desc == "Third commit message"

    # Verify the commits are in order (oldest to newest)
    assert commits[0].node != base_rev
    assert commits[1].node != base_rev
    assert commits[2].node != base_rev
