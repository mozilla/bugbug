# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Replaying a strategy over a timeline and finding when it catches each regression.

A run of a test on a push catches every regression of that test since the
previous run, so a regression is detected by the first push at or after its
culprit where the strategy runs one of its labels. Perfherder only raises the
alert once ``fore_window`` values follow the change, so the alert surfaces at
the ``fore_window``-th run of that label counted from the detecting one.
"""

import bisect
import logging
from dataclasses import dataclass

from bugbug.perf.simulation.strategies import ALL, Decision, Strategy
from bugbug.perf.simulation.timeline import Regression, Timeline

logger = logging.getLogger(__name__)

# Perfherder's default fore window: values needed after a change to alert.
FORE_WINDOW = 12


@dataclass
class Schedule:
    """What a strategy ran on each push of a timeline."""

    decisions: list[Decision]
    batch_indices: list[int]
    partial_runs: dict[str, list[int]]

    def __post_init__(self) -> None:
        self._runs_cache: dict[str | None, list[int]] = {}

    def runs_of(self, label: str | None) -> list[int]:
        """Indices of the pushes where ``label`` ran; ``None`` means any full batch."""
        if label is None or label not in self.partial_runs:
            return self.batch_indices
        cached = self._runs_cache.get(label)
        if cached is None:
            cached = sorted(set(self.batch_indices) | set(self.partial_runs[label]))
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
        elif decision is not None:
            for label in decision:
                partial_runs.setdefault(label, []).append(push.index)
    return Schedule(decisions, batch_indices, partial_runs)


@dataclass
class Detection:
    """When a schedule catches one regression."""

    regression: Regression
    label: str | None
    previous_index: int | None
    detected_index: int | None
    surfaced_index: int | None

    def hours(self, timeline: Timeline, index: int | None) -> float | None:
        if index is None:
            return None
        return timeline.hours(self.regression.push_index, index)

    def detect_hours(self, timeline: Timeline) -> float | None:
        return self.hours(timeline, self.detected_index)

    def surface_hours(self, timeline: Timeline) -> float | None:
        return self.hours(timeline, self.surfaced_index)

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


def detect(
    schedule: Schedule, timeline: Timeline, fore_window: int = FORE_WINDOW
) -> list[Detection]:
    """Find the detecting and surfacing push of every regression of the timeline.

    A regression with several regressed labels is caught by whichever runs
    first. Detection uses the whole timeline, so a regression near its end
    may be detected but not yet surfaced, or not detected at all.
    """
    detections = []
    for regression in timeline.regressions:
        best: Detection | None = None
        labels = sorted(regression.labels) if regression.labels else [None]
        for label in labels:
            runs = schedule.runs_of(label)
            position = bisect.bisect_left(runs, regression.push_index)
            detected = runs[position] if position < len(runs) else None
            if best is not None and (
                detected is None
                or (best.detected_index is not None and detected >= best.detected_index)
            ):
                continue
            previous = runs[position - 1] if position > 0 else None
            surfaced_position = position + fore_window - 1
            surfaced = (
                runs[surfaced_position] if surfaced_position < len(runs) else None
            )
            best = Detection(regression, label, previous, detected, surfaced)
        assert best is not None
        detections.append(best)
    return detections
