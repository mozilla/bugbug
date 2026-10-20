# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os

import hglib
import pytest

from scripts import regressor_finder


@pytest.fixture
def hg_repo(tmp_path):
    repo_dir = str(tmp_path)
    hglib.init(repo_dir)
    os.environ["USER"] = "app"
    hg = hglib.open(repo_dir)

    def commit(name, contents):
        path = os.path.join(repo_dir, name)
        new = not os.path.exists(path)
        with open(path, "w") as f:
            f.write(contents)
        if new:
            hg.add(files=[path.encode("utf-8")])
        _, node = hg.commit(message=f"Change {name}", user="app")
        return node.decode("ascii")

    yield hg, commit

    hg.close()


def test_get_deleted_lines():
    patch = b"""diff --git a/dom/a.cpp b/dom/a.cpp
--- a/dom/a.cpp
+++ b/dom/a.cpp
@@ -2 +2 @@
-int b = 2;
+int b = 3;
@@ -5,3 +5,0 @@
-
-// A comment.
-int e = 5;
diff --git a/dom/b.sql b/dom/b.sql
--- a/dom/b.sql
+++ b/dom/b.sql
@@ -1,2 +1,1 @@
--- A comment which looks like a header.
-SELECT 1;
+SELECT 2;
diff --git a/dom/new.cpp b/dom/new.cpp
new file mode 100644
--- /dev/null
+++ b/dom/new.cpp
@@ -0,0 +1,1 @@
+int x;
"""

    assert regressor_finder.get_deleted_lines(patch) == {
        "dom/a.cpp": [2, 7],
        "dom/b.sql": [1, 2],
    }


def test_find_bug_introducing_revs(hg_repo):
    hg, commit = hg_repo

    original = commit("a.cpp", "int a = 1;\nint b = 2;\nint c = 3;\n")
    bug_introducing = commit("a.cpp", "int a = 1;\nint b = 42;\nint c = 3;\n")
    ignored = commit("a.cpp", "int a = 1;\nint b = 42;\nint c = 4;\n")
    whitespace_only = commit("a.cpp", "int  a = 1;\nint b = 42;\nint c = 4;\n")
    bug_fixing = commit("a.cpp", "int  a = 0;\nint b = 2;\nint c = 5;\n")

    assert regressor_finder.find_bug_introducing_revs(hg, bug_fixing, {}) == {
        original,
        bug_introducing,
        ignored,
    }
    assert whitespace_only not in regressor_finder.find_bug_introducing_revs(
        hg, bug_fixing, {}
    )

    assert regressor_finder.find_bug_introducing_revs(
        hg, bug_fixing, {"a.cpp": [ignored]}
    ) == {original, bug_introducing}


def test_find_bug_introducing_revs_skips_non_source_files(hg_repo):
    hg, commit = hg_repo

    commit("README.txt", "Some text.\n")
    bug_fixing = commit("README.txt", "Some other text.\n")

    assert regressor_finder.find_bug_introducing_revs(hg, bug_fixing, {}) == set()
