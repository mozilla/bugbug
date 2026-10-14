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
