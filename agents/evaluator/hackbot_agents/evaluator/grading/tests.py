# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Score test results after a patch against the same tests before it.

Results are in the platform runner's shape: ``{test: {"status", "subtests"}}``.
"""

from agent_tools.firefox.tools.run_tests import PASSING


def file_score(entry: dict | None) -> float:
    """Share of a test file that passes: its subtests, or the test itself."""
    if not entry or entry["status"] not in PASSING:
        return 0.0
    subtests = entry["subtests"].values()
    return sum(s in PASSING for s in subtests) / len(subtests) if subtests else 1.0


def score(baseline: dict[str, dict], after: dict[str, dict]) -> dict[str, float]:
    """Each failing file's gain as a share of its headroom, and lost subtests."""
    gains, regressions = [], 0
    for test, base in baseline.items():
        before, now = file_score(base), file_score(after.get(test))
        if before < 1.0:
            gains.append(max(now - before, 0.0) / (1.0 - before))
        subtests = (after.get(test) or {}).get("subtests", {})
        regressions += sum(
            s in PASSING and subtests.get(name) not in PASSING
            for name, s in base["subtests"].items()
        )
    return {
        "test_progress": sum(gains) / len(gains) if gains else 0.0,
        "test_regressions": float(regressions),
    }
