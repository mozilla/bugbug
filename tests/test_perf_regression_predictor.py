# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from bugbug.models.perf_regression_predictor import (
    build_model_input,
    clean_commit_message,
    combine_commit_messages,
    diff_to_structured_text,
)
from scripts.perf_regression_predictor import (
    extract_commit_message_from_patch,
)

RAW_DIFF = """\
diff --git a/widget.py b/widget.py
index 1111111..2222222 100644
--- a/widget.py
+++ b/widget.py
@@ -1 +1 @@
-old_value = 1
+new_value = 2
 context
"""

HG_DIFF = """\
diff -r abcdef123456 widget.py
--- a/widget.py
+++ b/widget.py
@@ -1 +1 @@
-old_value = 1
+new_value = 2
 context
"""

BINARY_DIFF = """\
diff --git a/image.png b/image.png
index 1111111..2222222 100644
Binary files a/image.png and b/image.png differ
"""


def test_clean_commit_message_preserves_body() -> None:
    message = "[wpt PR 55677] - Rename tests\n\nKeep this body [verbatim]."
    assert clean_commit_message(message) == (
        "Rename tests\n\nKeep this body [verbatim]."
    )


def test_clean_commit_message_removes_bug_number_prefixes() -> None:
    messages = {
        "Bug 123456 - Improve rendering": "Improve rendering",
        "bug #123456: Improve rendering": "Improve rendering",
        "[PATCH] Bug 123456. Improve rendering": "Improve rendering",
        "Bug 123456 - [performance] Improve rendering": "Improve rendering",
    }
    for message, expected in messages.items():
        assert clean_commit_message(message) == expected


def test_combine_commit_messages_cleans_each_subject() -> None:
    assert combine_commit_messages(
        [
            "Bug 123456 - Improve rendering\n\nFirst body.",
            "[PATCH] Bug 789012 - Avoid repeated work\n\nSecond body.",
        ]
    ) == ("Improve rendering\n\nFirst body.\n\nAvoid repeated work\n\nSecond body.")


def test_combine_commit_messages_drops_empty_messages() -> None:
    assert (
        combine_commit_messages(
            [
                "",
                "   ",
                "Bug 123456 - Improve rendering",
            ]
        )
        == "Improve rendering"
    )


def test_diff_to_structured_text() -> None:
    assert (
        diff_to_structured_text(RAW_DIFF)
        == """\
<FILE>
  widget.py
  <REMOVED>
      old_value = 1
  </REMOVED>
  <ADDED>
      new_value = 2
  </ADDED>
</FILE>"""
    )


def test_diff_to_structured_text_mercurial_diff() -> None:
    assert (
        diff_to_structured_text(HG_DIFF)
        == """\
<FILE>
  widget.py
  <REMOVED>
      old_value = 1
  </REMOVED>
  <ADDED>
      new_value = 2
  </ADDED>
</FILE>"""
    )


def test_diff_to_structured_text_renamed_file() -> None:
    diff = """\
diff --git a/old_widget.py b/new_widget.py
similarity index 91%
rename from old_widget.py
rename to new_widget.py
--- a/old_widget.py
+++ b/new_widget.py
@@ -1 +1 @@
-old_value = 1
+new_value = 2
"""
    assert (
        diff_to_structured_text(diff)
        == """\
<FILE>
  new_widget.py
  <REMOVED>
      old_value = 1
  </REMOVED>
  <ADDED>
      new_value = 2
  </ADDED>
  File renamed from old_widget.py.
</FILE>"""
    )


def test_diff_to_structured_text_binary_file() -> None:
    assert (
        diff_to_structured_text(BINARY_DIFF)
        == """\
<FILE>
  image.png
  Binary file changed.
</FILE>"""
    )


def test_build_model_input_cleans_commit_message() -> None:
    prompt = build_model_input("[PATCH] Bug 123456 - Make it faster", RAW_DIFF)
    assert prompt.startswith(
        "<COMMIT_MESSAGE>\nMake it faster\n</COMMIT_MESSAGE>\n<FILE>"
    )
    assert "[PATCH]" not in prompt
    assert "Bug 123456" not in prompt


def test_build_model_input_allows_missing_commit_message() -> None:
    prompt = build_model_input(None, RAW_DIFF)
    assert prompt.startswith("<COMMIT_MESSAGE>\n\n</COMMIT_MESSAGE>\n<FILE>")
    assert "widget.py" in prompt


def test_extract_commit_message_from_git_format_patch() -> None:
    patch = """\
From abcdef Mon Sep 17 00:00:00 2001
From: Developer <developer@example.com>
Subject: [PATCH] Speed up rendering

Avoid unnecessary work in the hot path.

---
 widget.py | 2 +-
diff --git a/widget.py b/widget.py
"""
    assert extract_commit_message_from_patch(patch) == (
        "[PATCH] Speed up rendering\n\nAvoid unnecessary work in the hot path."
    )


def test_extract_commit_message_from_mercurial_export() -> None:
    patch = """\
# HG changeset patch
# User Developer <developer@example.com>
# Date 123456 0
# Node ID abc
# Parent  def
Speed up rendering

Avoid unnecessary work in the hot path.

diff -r def -r abc widget.py
"""
    assert extract_commit_message_from_patch(patch) == (
        "Speed up rendering\n\nAvoid unnecessary work in the hot path."
    )
