# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Chronological optimize and eval windows.

Strategies are tuned on the older part of the history and judged on the
newer part, never the other way round, since regressions cluster in time and
stateful strategies carry state across pushes. Both windows start after a
warm-up of pushes so a cold-start batch never counts.
"""

from datetime import datetime, timedelta

from bugbug.perf.simulation.metrics import Window
from bugbug.perf.simulation.timeline import Timeline

DEFAULT_WARMUP_PUSHES = 50


def default_bounds(timeline: Timeline) -> tuple[datetime, datetime]:
    """From the first regression's culprit push to just after the last one's.

    Pushes before the first alert of the snapshot are never culprits, and
    pushes after the last labeled one are not settled yet, so regressions
    only exist between the two.
    """
    if not timeline.regressions:
        return timeline.start, timeline.end + timedelta(seconds=1)
    return (
        timeline.regressions[0].time,
        timeline.regressions[-1].time + timedelta(seconds=1),
    )


def make_windows(
    timeline: Timeline,
    optimize_until: datetime,
    since: datetime | None = None,
    until: datetime | None = None,
    warmup_pushes: int = DEFAULT_WARMUP_PUSHES,
) -> tuple[Window, Window]:
    """Split ``[since, until)`` at ``optimize_until`` into an optimize and an eval window."""
    default_since, default_until = default_bounds(timeline)
    since = since or default_since
    until = until or default_until
    if warmup_pushes and len(timeline.pushes) > warmup_pushes:
        since = max(since, timeline.pushes[warmup_pushes].time)
    if not since < optimize_until < until:
        raise ValueError(
            f"optimize_until {optimize_until} must lie inside [{since}, {until})"
        )
    return Window(since, optimize_until), Window(optimize_until, until)
