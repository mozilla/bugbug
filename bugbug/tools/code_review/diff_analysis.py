# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Parse a unified diff into structured test signals used by risk scoring.

The diff analyzer answers one question deterministically, so we don't have to
ask the model to eyeball it: did the patch include test changes, and how much?
The resulting signal feeds both the risk/complexity scorer and the review
prompt. The set of changed non-test paths is also returned so the existing-test
coverage lookup (``test_coverage``) knows what to probe for in mozilla-central.

Ported from qreviews (qreviews/diff_analysis.py); the hunk-anchor tracking used
there to validate inline comments is dropped, since bugbug validates generated
comments against the patch set via ``utils.find_comment_scope``.
"""

import re
from dataclasses import dataclass, field

# Mozilla-flavoured test-path matchers. Applied to the new-side path
# of each `diff --git a/X b/Y` (or to X if the file was deleted).
_TEST_DIR_RE = re.compile(
    r"(^|/)(tests?|xpcshell|mochitests?|gtests?|reftests?|crashtests?|"
    r"jsapi-tests?|web-platform/tests?|googletest)(/|$)"
)
_TEST_FILENAME_RES = (
    re.compile(r"(^|/)test_[^/]+$"),
    re.compile(r"[^/]*_test\.[A-Za-z0-9]+$"),
    re.compile(r"[^/]*\.test\.[A-Za-z0-9]+$"),
    re.compile(r"(^|/)head[^/]*\.js$"),
    re.compile(r"(^|/)browser_[^/]+\.js$"),
)


def _is_test_path(path: str) -> bool:
    if not path:
        return False
    if _TEST_DIR_RE.search(path):
        return True
    return any(r.search(path) for r in _TEST_FILENAME_RES)


_DIFF_GIT_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)\s*$")
_HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_len>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_len>\d+))? @@"
)

# Possible values of DiffStats.in_diff_test_signal.
IN_DIFF_TEST_SIGNALS = ("absent", "sparse", "adequate", "tests_only", "no_code_change")


@dataclass(frozen=True)
class DiffStats:
    files_changed: int
    test_files_changed: int
    non_test_files_changed: int
    lines_added: int
    test_lines_added: int
    non_test_lines_added: int
    # One of IN_DIFF_TEST_SIGNALS.
    in_diff_test_signal: str
    non_test_paths: list[str] = field(default_factory=list)


def _classify_signal(
    *, non_test_files: int, test_files: int, non_test_added: int, test_added: int
) -> str:
    if non_test_files == 0 and test_files == 0:
        return "no_code_change"
    if non_test_files == 0:
        return "tests_only"
    if test_files == 0:
        return "absent"
    # Both kinds present — ratio decides.
    if non_test_added <= 0:
        # All deletions on non-test side; test additions are still meaningful.
        return "adequate" if test_added > 0 else "sparse"
    ratio = test_added / non_test_added
    return "adequate" if ratio >= 0.3 else "sparse"


def analyze_diff(raw_diff: str) -> DiffStats:
    """Walk a unified diff once and emit a DiffStats."""
    files_changed = 0
    test_files = 0
    non_test_files = 0
    total_added = 0
    test_added = 0
    non_test_added = 0
    non_test_paths: list[str] = []

    current_path: str | None = None
    current_is_test = False
    in_hunk = False

    for line in raw_diff.splitlines():
        m = _DIFF_GIT_RE.match(line)
        if m:
            files_changed += 1
            # Use the new-side path; on a deletion this still names the
            # original file (Phabricator passes the same path on both sides).
            current_path = m.group(2)
            current_is_test = _is_test_path(current_path)
            if current_is_test:
                test_files += 1
            else:
                non_test_files += 1
                non_test_paths.append(current_path)
            in_hunk = False
            continue

        if current_path is None:
            continue

        if _HUNK_RE.match(line):
            in_hunk = True
            continue

        if not in_hunk or not line:
            continue

        if line[0] == "+":
            total_added += 1
            if current_is_test:
                test_added += 1
            else:
                non_test_added += 1
        elif line[0] not in "- \\":
            # Unexpected line inside a hunk (e.g. a stray header from a
            # malformed diff). Drop out of hunk mode to resync.
            in_hunk = False

    signal = _classify_signal(
        non_test_files=non_test_files,
        test_files=test_files,
        non_test_added=non_test_added,
        test_added=test_added,
    )

    return DiffStats(
        files_changed=files_changed,
        test_files_changed=test_files,
        non_test_files_changed=non_test_files,
        lines_added=total_added,
        test_lines_added=test_added,
        non_test_lines_added=non_test_added,
        in_diff_test_signal=signal,
        non_test_paths=non_test_paths,
    )


def format_test_signal_block(
    stats: DiffStats,
    *,
    coverage_block: str | None = None,
) -> str:
    """Render the structured signal block prepended to scoring/review messages.

    The optional ``coverage_block`` is the existing-coverage layer rendered by
    ``test_coverage.format_coverage_block``; the two-layer rendering lives here
    so it isn't duplicated across scoring and review.
    """
    lines = [
        "<test_signals>",
        "Pre-computed from the diff (not the model):",
        f"  files_changed={stats.files_changed}",
        f"  test_files_changed={stats.test_files_changed}",
        f"  non_test_files_changed={stats.non_test_files_changed}",
        f"  lines_added={stats.lines_added} "
        f"(test={stats.test_lines_added}, non_test={stats.non_test_lines_added})",
        f"  in_diff_test_signal={stats.in_diff_test_signal}",
    ]
    if coverage_block:
        lines.append("")
        lines.append(coverage_block.rstrip())
    lines.append("</test_signals>")
    return "\n".join(lines)
