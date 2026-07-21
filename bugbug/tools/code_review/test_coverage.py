# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Probe mozilla-central via searchfox for existing tests of a revision's files.

This complements ``diff_analysis``. The diff analyzer tells us whether the patch
itself includes test changes; this module tells us whether existing tests in the
tree already reference the touched non-test files, even when the patch doesn't
add tests.

The signal is intentionally a hint, not a guarantee — basename queries can hit
unrelated same-named files. Risk scoring treats it as one input among many.

Ported from qreviews (qreviews/test_coverage.py), with the searchfox-cli
subprocess backend swapped for bugbug's async ``AsyncSearchfoxClient`` (the same
client the review agent's searchfox tools already use).
"""

import os
from dataclasses import dataclass, field
from logging import getLogger

from bugbug.tools.code_review.langchain_tools import _get_client

logger = getLogger(__name__)

# Cap how many non-test files we look up per revision. searchfox queries are
# cheap but not free; large refactors would otherwise issue dozens of requests.
DEFAULT_FILE_CAP = 10

# Per-query hit cap. We only need to know "are there any tests" — a small
# limit keeps the result bounded.
PER_QUERY_LIMIT = 5


@dataclass(frozen=True)
class ExistingCoverage:
    covered_paths: dict[str, list[str]] = field(default_factory=dict)
    uncovered_paths: list[str] = field(default_factory=list)
    # "covered" | "partial" | "uncovered" | "no_non_test_files" |
    # "skipped_large_diff" | "skipped_searchfox_error"
    coverage_signal: str = "skipped_searchfox_error"
    # Total non-test files in the diff, regardless of cap.
    candidate_count: int = 0


def _basename_no_ext(path: str) -> str:
    """Strip up to two extensions so a basename collapses to its module name.

    Two so Mozilla suffixes like ``foo.sys.mjs`` collapse to ``foo``, but no
    further so single-dot names (``some.module.cpp``) keep ``some.module``.
    """
    base = os.path.basename(path)
    for _ in range(2):
        name, ext = os.path.splitext(base)
        if not ext:
            break
        base = name
    return base


async def lookup_existing_coverage(
    non_test_paths: list[str],
    *,
    file_cap: int = DEFAULT_FILE_CAP,
) -> ExistingCoverage:
    """Run a small searchfox query per non-test path and aggregate.

    Returns an ExistingCoverage describing the per-file hit lists and an overall
    ``coverage_signal``. Any searchfox failure degrades to a skipped signal
    rather than raising — coverage is advisory, never required.
    """
    if not non_test_paths:
        return ExistingCoverage(coverage_signal="no_non_test_files", candidate_count=0)

    candidate_count = len(non_test_paths)
    skipped_large = candidate_count > file_cap
    paths = non_test_paths[:file_cap] if skipped_large else list(non_test_paths)

    client = _get_client()
    covered: dict[str, list[str]] = {}
    uncovered: list[str] = []

    for path in paths:
        query = _basename_no_ext(path)
        if not query or len(query) < 3:
            # Generic short names like 'x' or 'io' produce too much noise.
            uncovered.append(path)
            continue
        try:
            results = await client.search(
                query=query,
                tests="only",
                limit=PER_QUERY_LIMIT,
            )
        except Exception as e:  # searchfox raises plain Exception
            logger.warning("searchfox coverage lookup failed at %s: %s", path, e)
            return ExistingCoverage(
                covered_paths=covered,
                uncovered_paths=uncovered + paths[len(covered) + len(uncovered) :],
                coverage_signal="skipped_searchfox_error",
                candidate_count=candidate_count,
            )

        # Drop self-references — the queried file can show up if it shares a
        # basename with a test path by sheer regex.
        hits: list[str] = []
        seen: set[str] = set()
        for hit_path, _line, _content in results:
            if hit_path == path or hit_path in seen:
                continue
            seen.add(hit_path)
            hits.append(hit_path)

        if hits:
            covered[path] = hits
        else:
            uncovered.append(path)

    if skipped_large:
        signal = "skipped_large_diff"
    elif covered and uncovered:
        signal = "partial"
    elif covered:
        signal = "covered"
    else:
        signal = "uncovered"

    return ExistingCoverage(
        covered_paths=covered,
        uncovered_paths=uncovered,
        coverage_signal=signal,
        candidate_count=candidate_count,
    )


def format_coverage_block(coverage: ExistingCoverage) -> str | None:
    """Render the existing-coverage layer for the ``<test_signals>`` block.

    Returns None when there's nothing meaningful to show.
    """
    if coverage.coverage_signal == "no_non_test_files":
        return None
    lines = [
        "Existing tests referencing changed non-test files (searchfox lookup):",
        f"  coverage_signal={coverage.coverage_signal}",
        f"  candidate_files={coverage.candidate_count}",
    ]
    if coverage.covered_paths:
        lines.append("  covered:")
        for src, tests in coverage.covered_paths.items():
            sample = ", ".join(tests[:3])
            more = "" if len(tests) <= 3 else f" (+{len(tests) - 3} more)"
            lines.append(f"    - {src} <- {sample}{more}")
    if coverage.uncovered_paths:
        lines.append("  uncovered:")
        for src in coverage.uncovered_paths:
            lines.append(f"    - {src}")
    return "\n".join(lines)
