# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from types import SimpleNamespace

import pytest

from bugbug.models.perf_regression_predictor import (
    CommitMessageCleaner,
    DiffStructurer,
    PatchCommitMessageExtractor,
    PerfRegressionPredictorModel,
)

clean_commit_message = CommitMessageCleaner()
diff_to_structured_text = DiffStructurer()
extract_commit_message_from_patch = PatchCommitMessageExtractor()

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


def test_diff_to_structured_text_full_hg_export() -> None:
    # A full `hg export --git` payload carries the changeset header and commit
    # message before the diff; the preamble must be ignored, including message
    # lines that happen to start with "+" or "-".
    export = """\
# HG changeset patch
# User Developer <developer@example.com>
# Date 1600000000 0
# Node ID abcdef123456
# Parent  123456abcdef
Bug 123456 - Improve rendering

Body mentions -old_value and +new_value.

diff --git a/widget.py b/widget.py
--- a/widget.py
+++ b/widget.py
@@ -1 +1 @@
-old_value = 1
+new_value = 2
 context
"""
    assert (
        diff_to_structured_text(export)
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


# `hg export --git` (what the http service feeds the model) and
# `git format-patch` both write the binary payload as base85 after a
# "GIT binary patch" line instead of git diff's "Binary files ... differ".
HG_GIT_BINARY_EXPORT = """\
# HG changeset patch
# User Developer <developer@example.com>
# Date 1600000000 0
# Node ID node1hash
# Parent  node0hash
Bug 123456 - Update the icon

diff --git a/image.png b/image.png
index 6b96b519f4a23610bd13394d59ee479819ef03b6..286513b75d88656e0300bac45ea6c656b57c80ea
GIT binary patch
literal 25
gc%17D@N?(olHy|E<Y3P)$w)0?NUAJJEoS%+081_h3IG5A

diff --git a/loop.py b/loop.py
--- a/loop.py
+++ b/loop.py
@@ -1 +1 @@
-slow()
+fast()
"""

HG_GIT_NEW_BINARY_EXPORT = """\
diff --git a/image.png b/image.png
new file mode 100644
index 0000000000000000000000000000000000000000..6b96b519f4a23610bd13394d59ee479819ef03b6
GIT binary patch
literal 23
ec%17D@N?(olHy`uWMWRr%u6h)WJpOYNdy2quLaKl

"""

# Built from lines so the signature separator keeps its trailing space.
GIT_FORMAT_PATCH_BINARY = "\n".join(
    [
        "diff --git a/image.png b/image.png",
        "index 6b96b519f4a23610bd13394d59ee479819ef03b6..286513b75d88656e0300bac45ea6c656b57c80ea 100644",
        "GIT binary patch",
        "literal 25",
        "gcmeAS@N?(olHy|E<Y3P)$w)0?NUAJJEoS%+081_h3IG5A",
        "",
        "literal 23",
        "ecmeAS@N?(olHy`uWMWRr%u6h)WJpOYNdy2quLaKl",
        "",
        "-- ",
        "2.43.0",
        "",
    ]
)

HG_PLAIN_BINARY_EXPORT = """\
diff -r 6815d1c9fb30 -r 430f3d6416e9 image.png
Binary file image.png has changed
"""

BINARY_FILE_BLOCK = """\
<FILE>
  image.png
  Binary file changed.
</FILE>"""


def test_diff_to_structured_text_binary_file() -> None:
    assert diff_to_structured_text(BINARY_DIFF) == BINARY_FILE_BLOCK


@pytest.mark.parametrize(
    "diff",
    [HG_GIT_NEW_BINARY_EXPORT, GIT_FORMAT_PATCH_BINARY, HG_PLAIN_BINARY_EXPORT],
    ids=["hg-export-git-new-file", "git-format-patch", "hg-export-plain"],
)
def test_diff_to_structured_text_binary_file_other_formats(diff) -> None:
    # Every tool's binary notation maps to the marker the model was trained on.
    assert diff_to_structured_text(diff) == BINARY_FILE_BLOCK


def test_diff_to_structured_text_binary_payload_does_not_leak() -> None:
    # The base85 payload after "GIT binary patch" is dropped, and the text
    # file that follows it is still parsed normally.
    assert (
        diff_to_structured_text(HG_GIT_BINARY_EXPORT)
        == BINARY_FILE_BLOCK
        + """
<FILE>
  loop.py
  <REMOVED>
      slow()
  </REMOVED>
  <ADDED>
      fast()
  </ADDED>
</FILE>"""
    )


def test_build_model_input_cleans_commit_message() -> None:
    model = PerfRegressionPredictorModel()
    prompt = model.build_model_input("[PATCH] Bug 123456 - Make it faster", RAW_DIFF)
    assert prompt.startswith(
        "<COMMIT_MESSAGE>\nMake it faster\n</COMMIT_MESSAGE>\n<FILE>"
    )
    assert "[PATCH]" not in prompt
    assert "Bug 123456" not in prompt


def test_build_model_input_allows_missing_commit_message() -> None:
    model = PerfRegressionPredictorModel()
    prompt = model.build_model_input(None, RAW_DIFF)
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


# Tokenizers without a declared limit report this sentinel instead of a size.
_NO_TOKENIZER_LIMIT = int(1e30)


def _model_with_limits(
    tokenizer_limit: int, position_embeddings: int, padding_idx: int | None
) -> PerfRegressionPredictorModel:
    model = PerfRegressionPredictorModel()
    model.tokenizer = SimpleNamespace(model_max_length=tokenizer_limit)
    embeddings = (
        SimpleNamespace(padding_idx=padding_idx)
        if padding_idx is not None
        else SimpleNamespace()
    )
    model.transformer_model = SimpleNamespace(
        config=SimpleNamespace(max_position_embeddings=position_embeddings),
        base_model=SimpleNamespace(embeddings=embeddings),
    )
    return model


@pytest.mark.parametrize(
    "tokenizer_limit, position_embeddings, padding_idx, expected",
    [
        # The published CodeBERT checkpoint: RoBERTa numbers positions from
        # padding_idx + 1, so a 514-entry table embeds 512 tokens.
        (512, 514, 1, 512),
        # No declared tokenizer limit must not let the raw table size win.
        (_NO_TOKENIZER_LIMIT, 514, 1, 512),
        # A tokenizer that overstates the limit is capped the same way.
        (514, 514, 1, 512),
        # A tighter tokenizer limit is honoured.
        (256, 514, 1, 256),
        # BERT-style embeddings start at 0, so every position is usable.
        (_NO_TOKENIZER_LIMIT, 512, None, 512),
    ],
)
def test_max_length_never_exceeds_usable_positions(
    tokenizer_limit, position_embeddings, padding_idx, expected
) -> None:
    model = _model_with_limits(tokenizer_limit, position_embeddings, padding_idx)
    assert model.max_length == expected


def test_diff_to_structured_text_ignores_git_format_patch_signature() -> None:
    patch = "\n".join(
        [
            "diff --git a/loop.py b/loop.py",
            "--- a/loop.py",
            "+++ b/loop.py",
            "@@ -1 +1 @@",
            "-slow()",
            "+fast()",
            "-- ",
            "2.43.0",
            "",
        ]
    )
    assert (
        diff_to_structured_text(patch)
        == """\
<FILE>
  loop.py
  <REMOVED>
      slow()
  </REMOVED>
  <ADDED>
      fast()
  </ADDED>
</FILE>"""
    )


def test_build_prompt_drops_text_beyond_the_token_window() -> None:
    model = _model_with_limits(512, 514, 1)
    assert model.max_input_chars == 512 * 64

    # A diff far larger than anything 512 tokens could cover.
    huge_diff = "diff --git a/big.py b/big.py\n@@ -1 +1 @@\n" + "".join(
        f"+line {i}\n" for i in range(50_000)
    )
    assert len(huge_diff) > 10 * model.max_input_chars

    prompt = model._build_prompt({"commit_message": "Bug 1 - Big", "diff": huge_diff})

    # The prompt is bounded (structuring adds tags and indentation, so allow
    # for that), and it still starts with the same text as the full input.
    assert len(prompt) < 2 * model.max_input_chars
    assert prompt.startswith(
        "<COMMIT_MESSAGE>\nBig\n</COMMIT_MESSAGE>\n<FILE>\n  big.py\n  <ADDED>\n      line 0\n"
    )

    # A small diff is passed through unchanged.
    small = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert model._build_prompt({"commit_message": None, "diff": small}) == (
        model.build_model_input(None, small)
    )
