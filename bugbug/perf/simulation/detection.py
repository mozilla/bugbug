# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Replaying a strategy over a timeline and finding when it catches each regression.

A run of a test on a push catches every regression of that test since the
previous run, so a regression is detected by the first push at or after its
culprit where the strategy runs one of its labels.
"""

import bisect
import logging
from dataclasses import dataclass

from bugbug.perf.simulation.strategies import ALL, Decision, Strategy
from bugbug.perf.simulation.timeline import Regression, Timeline

logger = logging.getLogger(__name__)


@dataclass
class Schedule:
    """What a strategy ran on each push of a timeline.

    ``batch_suites`` holds, by position in ``batch_indices``, the suite each
    batch ran: the labels that existed at its push, or ``None`` where no
    full-suite push at or before it recorded them.
    """

    decisions: list[Decision]
    batch_indices: list[int]
    partial_runs: dict[str, list[int]]
    batch_suites: list[frozenset[str] | None]

    def __post_init__(self) -> None:
        self._runs_cache: dict[str | None, list[int]] = {}

    def runs_of(self, label: str | None) -> list[int]:
        """Indices of the pushes where ``label`` ran; ``None`` means any full batch.

        A batch counts only when its suite held the label: a label added
        later, or gone by then, did not run in it. A batch whose suite is
        unknown counts for every label, since an alert on a label proves it
        existed and the jobs that would show it have been dropped.
        """
        if label is None:
            return self.batch_indices
        cached = self._runs_cache.get(label)
        if cached is None:
            cached = [
                index
                for index, suite in zip(self.batch_indices, self.batch_suites)
                if suite is None or label in suite
            ]
            partial = self.partial_runs.get(label)
            if partial:
                cached = sorted(set(cached) | set(partial))
            self._runs_cache[label] = cached
        return cached

    def jobs_at(self, index: int, timeline: Timeline) -> int:
        decision = self.decisions[index]
        if decision is None:
            return 0
        if decision == ALL:
            return len(timeline.suite_at(index))
        return len(decision)


def replay(strategy: Strategy, timeline: Timeline) -> Schedule:
    """Run ``strategy`` over every push of ``timeline`` in order.

    Pushes marked DONTBUILD run nothing and take no decision, as the decision
    task strips every task from them; their commits are still observed, so
    they count towards the next decision.
    """
    strategy.reset()
    decisions: list[Decision] = []
    batch_indices: list[int] = []
    partial_runs: dict[str, list[int]] = {}
    batch_suites: list[frozenset[str] | None] = []
    for push in timeline.pushes:
        strategy.observe(push)
        if push.dontbuild:
            decisions.append(None)
            continue
        decision = strategy.decide(push)
        if decision is not None and decision != ALL and not decision:
            decision = None
        decisions.append(decision)
        if decision == ALL:
            batch_indices.append(push.index)
            batch_suites.append(timeline.known_suite_at(push.index))
        elif decision is not None:
            for label in decision:
                partial_runs.setdefault(label, []).append(push.index)
    return Schedule(decisions, batch_indices, partial_runs, batch_suites)


@dataclass
class Detection:
    """When a schedule catches one regression, through the label that runs first.

    ``alternatives`` are the same regression caught through each of its other
    labels. The CI replay waits for all of them, since the label whose run is
    scheduled first need not be the one whose run completes first.
    """

    regression: Regression
    label: str | None
    previous_index: int | None
    detected_index: int | None
    alternatives: tuple["Detection", ...] = ()

    def candidates(self) -> tuple["Detection", ...]:
        """This detection and its alternatives: one per label of the regression."""
        return (self, *self.alternatives)

    def hours(self, timeline: Timeline, index: int | None) -> float | None:
        if index is None:
            return None
        return timeline.hours(self.regression.push_index, index)

    def detect_hours(self, timeline: Timeline) -> float | None:
        return self.hours(timeline, self.detected_index)

    @property
    def detect_pushes(self) -> int | None:
        if self.detected_index is None:
            return None
        return self.detected_index - self.regression.push_index

    @property
    def window_pushes(self) -> int | None:
        """Pushes a sheriff has to bisect: from the previous run to the detecting one."""
        if self.detected_index is None:
            return None
        if self.previous_index is None:
            return self.detected_index + 1
        return self.detected_index - self.previous_index


def candidates(schedule: Schedule, regression: Regression) -> list[Detection]:
    """One detection per regressed label, in label order, or one with no label.

    Each is where that label's runs catch the regression: the first run at
    or after the culprit, with the run before as the previous one. The
    detecting index is ``None`` when no such run exists.
    """
    labels = sorted(regression.labels) if regression.labels else [None]
    found = []
    for label in labels:
        runs = schedule.runs_of(label)
        position = bisect.bisect_left(runs, regression.push_index)
        detected = runs[position] if position < len(runs) else None
        previous = runs[position - 1] if position > 0 else None
        found.append(Detection(regression, label, previous, detected))
    return found


def detect(schedule: Schedule, timeline: Timeline) -> list[Detection]:
    """Find the detecting push of every regression of the timeline.

    A regression with several regressed labels is caught by whichever runs
    first, the others kept as its ``alternatives``. Detection uses the whole
    timeline, so a regression near its end may not be detected at all when
    no later run exists.
    """
    detections = []
    for regression in timeline.regressions:
        found = candidates(schedule, regression)
        best = min(
            found,
            key=lambda d: (d.detected_index is None, d.detected_index or 0),
        )
        best.alternatives = tuple(d for d in found if d is not best)
        detections.append(best)
    return detections
