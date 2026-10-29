# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Pin the unidiff behaviors bugbug depends on.

The code review tooling reads ``PatchedFile.path``, ``source_file`` and
``target_file`` directly and assumes the latter two keep their ``a/``/``b/``
prefixes (e.g. ``target_file[2:]``, ``"b/" + path``, ``== f"a/{path}"``).
These tests make sure an unidiff upgrade does not silently change that.
"""

import glob
import os

import pytest
from unidiff import PatchSet
from unidiff.errors import UnidiffParseError

from bugbug.tools.code_review.data_types import GeneratedReviewComment
from bugbug.tools.code_review.review_context import parse_diff_files
from bugbug.tools.code_review.utils import (
    convert_generated_comments_to_inline,
    format_patch_set,
    get_associated_file_to_function,
)
from bugbug.tools.core.data_types import InlineComment
from bugbug.tools.core.platforms.base import ReviewData
from bugbug.tools.core.platforms.patch_apply import (
    apply_patched_file,
    find_patched_file,
    get_file_after_stack,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures/phabricator")

MODIFIED_DIFF = (
    "diff --git a/dom/base/f.cpp b/dom/base/f.cpp\n"
    "--- a/dom/base/f.cpp\n"
    "+++ b/dom/base/f.cpp\n"
    "@@ -1,3 +1,3 @@\n"
    " a\n"
    "-b\n"
    "+B\n"
    " c\n"
)

ADDED_DIFF = (
    "diff --git a/new.txt b/new.txt\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/new.txt\n"
    "@@ -0,0 +1,2 @@\n"
    "+a\n"
    "+b\n"
)

REMOVED_DIFF = (
    "diff --git a/gone.txt b/gone.txt\n"
    "deleted file mode 100644\n"
    "--- a/gone.txt\n"
    "+++ /dev/null\n"
    "@@ -1,2 +0,0 @@\n"
    "-a\n"
    "-b\n"
)

RENAME_DIFF = (
    "diff --git a/old.txt b/new.txt\n"
    "similarity index 80%\n"
    "rename from old.txt\n"
    "rename to new.txt\n"
    "--- a/old.txt\n"
    "+++ b/new.txt\n"
    "@@ -1,2 +1,2 @@\n"
    " a\n"
    "-b\n"
    "+B\n"
)

PURE_RENAME_DIFF = (
    "diff --git a/old.txt b/new.txt\n"
    "similarity index 100%\n"
    "rename from old.txt\n"
    "rename to new.txt\n"
)

BINARY_DIFF = (
    "diff --git a/img.png b/img.png\n"
    "index 1111111..2222222 100644\n"
    "Binary files a/img.png and b/img.png differ\n"
)


# ---------------------------------------------------------------------------
# PatchedFile attributes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_diff, path, source_file, target_file, flags",
    [
        (
            MODIFIED_DIFF,
            "dom/base/f.cpp",
            "a/dom/base/f.cpp",
            "b/dom/base/f.cpp",
            {"modified"},
        ),
        (ADDED_DIFF, "new.txt", "/dev/null", "b/new.txt", {"added"}),
        (REMOVED_DIFF, "gone.txt", "a/gone.txt", "/dev/null", {"removed"}),
        (RENAME_DIFF, "new.txt", "a/old.txt", "b/new.txt", {"modified", "rename"}),
        (
            PURE_RENAME_DIFF,
            "new.txt",
            "a/old.txt",
            "b/new.txt",
            {"modified", "rename"},
        ),
        (BINARY_DIFF, "img.png", "a/img.png", "b/img.png", {"modified", "binary"}),
    ],
)
def test_patched_file_attributes(raw_diff, path, source_file, target_file, flags):
    (patched_file,) = PatchSet.from_string(raw_diff)

    assert patched_file.path == path
    # Callers strip these prefixes themselves, so they must be kept.
    assert patched_file.source_file == source_file
    assert patched_file.target_file == target_file

    assert patched_file.is_modified_file == ("modified" in flags)
    assert patched_file.is_added_file == ("added" in flags)
    assert patched_file.is_removed_file == ("removed" in flags)
    assert patched_file.is_rename == ("rename" in flags)
    assert patched_file.is_binary_file == ("binary" in flags)


@pytest.mark.parametrize("directory", ["c", "i", "o", "w", "1", "2"])
def test_patched_file_path_strips_only_the_diff_prefix(directory):
    # unidiff 1.0 also strips git mnemonic prefixes (c/, i/, w/, ...). Make
    # sure a real top-level directory with such a name survives behind a/ b/.
    raw_diff = (
        f"diff --git a/{directory}/f.txt b/{directory}/f.txt\n"
        f"--- a/{directory}/f.txt\n"
        f"+++ b/{directory}/f.txt\n"
        "@@ -1 +1 @@\n"
        "-a\n"
        "+b\n"
    )
    (patched_file,) = PatchSet.from_string(raw_diff)

    assert patched_file.path == f"{directory}/f.txt"
    assert patched_file.source_file == f"a/{directory}/f.txt"
    assert patched_file.target_file == f"b/{directory}/f.txt"


def test_patch_set_multiple_files_keep_order():
    patch_set = PatchSet.from_string(MODIFIED_DIFF + ADDED_DIFF + REMOVED_DIFF)

    assert [f.path for f in patch_set] == ["dom/base/f.cpp", "new.txt", "gone.txt"]
    assert [f.path for f in patch_set.modified_files] == ["dom/base/f.cpp"]
    assert [f.path for f in patch_set.added_files] == ["new.txt"]
    assert [f.path for f in patch_set.removed_files] == ["gone.txt"]


def test_patch_set_round_trips_to_string():
    raw_diff = MODIFIED_DIFF + ADDED_DIFF + RENAME_DIFF
    patch_set = PatchSet.from_string(raw_diff)

    assert str(patch_set) == raw_diff
    # apply_patched_file feeds str(patched_file) to whatthepatch.
    assert str(patch_set[0]) == MODIFIED_DIFF


def test_empty_patch_set():
    assert len(PatchSet.from_string("")) == 0


def test_malformed_diff_raises_unidiff_parse_error():
    with pytest.raises(UnidiffParseError):
        PatchSet.from_string("--- a/f\n+++ b/f\n@@ -1,3 +1,3 @@\n a\n")


# ---------------------------------------------------------------------------
# Hunk and Line attributes
# ---------------------------------------------------------------------------


def test_hunk_and_line_attributes():
    (patched_file,) = PatchSet.from_string(MODIFIED_DIFF)
    (hunk,) = patched_file

    assert (hunk.source_start, hunk.source_length) == (1, 3)
    assert (hunk.target_start, hunk.target_length) == (1, 3)
    assert [
        (
            line.is_context,
            line.is_added,
            line.is_removed,
            line.source_line_no,
            line.target_line_no,
            line.value,
        )
        for line in hunk
    ] == [
        (True, False, False, 1, 1, "a\n"),
        (False, False, True, 2, None, "b\n"),
        (False, True, False, None, 2, "B\n"),
        (True, False, False, 3, 3, "c\n"),
    ]
    assert hunk.source == [" a\n", "-b\n", " c\n"]
    assert hunk.target == [" a\n", "+B\n", " c\n"]
    # Hunks support indexing, which find_mixed_lines_range relies on.
    assert hunk[1].is_removed


# ---------------------------------------------------------------------------
# Real Phabricator diffs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    sorted(glob.glob(os.path.join(FIXTURES_DIR, "*.diff"))),
    ids=os.path.basename,
)
def test_phabricator_fixture_paths(fixture):
    with open(fixture) as f:
        raw_diff = f.read()

    patch_set = PatchSet.from_string(raw_diff)
    assert len(patch_set) == raw_diff.count("\ndiff --git ") + raw_diff.startswith(
        "diff --git "
    )

    for patched_file in patch_set:
        assert patched_file.source_file.startswith(("a/", "/dev/null"))
        assert patched_file.target_file.startswith(("b/", "/dev/null"))
        expected_path = (
            patched_file.source_file[2:]
            if patched_file.is_removed_file
            else patched_file.target_file[2:]
        )
        assert patched_file.path == expected_path


# ---------------------------------------------------------------------------
# bugbug helpers built on unidiff
# ---------------------------------------------------------------------------


def test_find_patched_file_by_path_and_prefixed_target():
    patch_set = PatchSet.from_string(MODIFIED_DIFF + RENAME_DIFF)

    assert find_patched_file(patch_set, "dom/base/f.cpp") is patch_set[0]
    assert find_patched_file(patch_set, "new.txt") is patch_set[1]
    assert find_patched_file(patch_set, "old.txt") is None


def test_apply_patched_file_pure_rename_and_binary_keep_content():
    (pure_rename,) = PatchSet.from_string(PURE_RENAME_DIFF)
    (binary,) = PatchSet.from_string(BINARY_DIFF)

    assert apply_patched_file("x\ny\n", pure_rename) == "x\ny\n"
    assert apply_patched_file("binary", binary) == "binary"


@pytest.mark.asyncio
async def test_get_file_after_stack_follows_rename_to_source():
    fetched = []

    async def fetch(path):
        fetched.append(path)
        return "a\nb\n"

    content = await get_file_after_stack(
        [PatchSet.from_string(RENAME_DIFF)], "b/new.txt", fetch
    )

    assert fetched == ["old.txt"]
    assert content == "a\nB\n"


def test_parse_diff_files_skips_removed_files():
    diff = MODIFIED_DIFF + ADDED_DIFF + REMOVED_DIFF + RENAME_DIFF

    assert parse_diff_files(diff) == {"dom/base/f.cpp", "new.txt"}


def test_format_patch_set_uses_prefixed_target_file():
    output = format_patch_set(PatchSet.from_string(MODIFIED_DIFF))

    assert output == ("Filename: b/dom/base/f.cpp\n1   a\n2 - b\n2 + B\n3   c\n\n")


def test_get_associated_file_to_function():
    patch_set = PatchSet.from_string(MODIFIED_DIFF + ADDED_DIFF)

    assert get_associated_file_to_function("-b", patch_set) == "dom/base/f.cpp"
    assert get_associated_file_to_function("missing", patch_set) is None


@pytest.mark.parametrize("file_path", ["dom/base/f.cpp", "b/dom/base/f.cpp"])
def test_convert_generated_comments_to_inline(file_path):
    comment = GeneratedReviewComment(
        file=file_path,
        code_line=2,
        comment="Comment",
        explanation="Explanation",
        order=1,
    )

    (inline,) = convert_generated_comments_to_inline(
        [comment], PatchSet.from_string(MODIFIED_DIFF)
    )

    assert inline.filename == "dom/base/f.cpp"
    assert inline.start_line == 2
    assert inline.on_removed_code is False
    assert (inline.hunk_start_line, inline.hunk_end_line) == (1, 2)


class _FakeReviewData(ReviewData):
    def __init__(self, raw_diff, comments):
        self.raw_diff = raw_diff
        self.comments = comments

    def get_patch_by_id(self, patch_id):
        raise NotImplementedError

    def get_all_inline_comments(self, comment_filter):
        yield 1, [c for c in self.comments if comment_filter(c)]

    def load_raw_diff_by_id(self, diff_id):
        return self.raw_diff


def test_retrieve_comments_with_hunks_matches_by_path():
    def make_comment(filename):
        return InlineComment(
            filename=filename,
            start_line=2,
            end_line=2,
            content="Comment",
            on_removed_code=False,
            is_generated=True,
        )

    review_data = _FakeReviewData(
        MODIFIED_DIFF + ADDED_DIFF,
        [
            make_comment("dom/base/f.cpp"),
            # Only modified files are considered.
            make_comment("new.txt"),
            make_comment("unknown.txt"),
        ],
    )

    results = list(review_data.retrieve_comments_with_hunks())

    assert len(results) == 1
    hunk, comment = results[0]
    assert comment.filename == "dom/base/f.cpp"
    assert (hunk.target_start, hunk.target_length) == (1, 3)
