# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Cost and detection metrics of a schedule over a window of the timeline."""

import collections
import random
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from bugbug.perf.simulation.detection import Detection, Schedule
from bugbug.perf.simulation.strategies import ALL
from bugbug.perf.simulation.timeline import Timeline

Latency = Literal["surface", "detect"]
Cost = Literal["batches", "jobs"]
Statistic = Literal["mean", "median", "p90", "max"]


@dataclass(frozen=True)
class Window:
    """Pushes with ``start <= time < end``; cost is counted and culprits are selected in it."""

    start: datetime
    end: datetime

    def contains(self, time: datetime) -> bool:
        return self.start <= time < self.end


def _summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p90": None, "max": None}
    ordered = sorted(values)
    return {
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p90": ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))],
        "max": ordered[-1],
    }


def bootstrap_mean(
    values: list[float], samples: int = 1000, seed: int = 0
) -> tuple[float, float] | None:
    """95 percent percentile bootstrap interval of the mean, None below two values."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(
        statistics.fmean(rng.choices(values, k=len(values))) for _ in range(samples)
    )
    return means[int(0.025 * samples)], means[min(samples - 1, int(0.975 * samples))]


def evaluate(
    timeline: Timeline,
    schedule: Schedule,
    detections: list[Detection],
    window: Window,
    bootstrap_samples: int = 0,
) -> dict[str, Any]:
    """Measure a schedule on the pushes and regressions whose time falls in ``window``.

    Cost counts the batches and jobs the schedule ran in the window. Detection
    covers the regressions whose culprit landed in the window, wherever they
    were caught: ``detect_hours`` is the time to the first run after the
    culprit and ``surface_hours`` the time until Perfherder would alert, over
    the regressions where that run exists in the timeline.
    """
    indices = [push.index for push in timeline.pushes if window.contains(push.time)]
    batch_indices = [i for i in indices if schedule.decisions[i] == ALL]
    jobs = sum(schedule.jobs_at(i, timeline) for i in indices)
    gaps_pushes = [b - a for a, b in zip(batch_indices, batch_indices[1:])]
    gaps_hours = [
        timeline.hours(a, b) for a, b in zip(batch_indices, batch_indices[1:])
    ]

    selected = [d for d in detections if window.contains(d.regression.time)]
    detected = [d for d in selected if d.detected_index is not None]
    surfaced = [d for d in detected if d.surfaced_index is not None]
    detect_hours = [d.detect_hours(timeline) or 0.0 for d in detected]
    surface_hours = [d.surface_hours(timeline) or 0.0 for d in surfaced]
    caught_at: collections.Counter = collections.Counter(
        (d.detected_index, d.label) for d in detected
    )

    result: dict[str, Any] = {
        "window": {
            "start": window.start.isoformat(),
            "end": window.end.isoformat(),
            "pushes": len(indices),
        },
        "batches": len(batch_indices),
        "jobs": jobs,
        "pushes_per_batch": _summary([float(g) for g in gaps_pushes]),
        "hours_between_batches": _summary(gaps_hours),
        "regressions": len(selected),
        "detected": len(detected),
        "surfaced": len(surfaced),
        "detected_ids": sorted(d.regression.summary_id for d in detected),
        "missed_ids": sorted(
            d.regression.summary_id for d in selected if d.detected_index is None
        ),
        "detect_hours": _summary(detect_hours),
        "surface_hours": _summary(surface_hours),
        "detect_pushes": _summary([float(d.detect_pushes or 0) for d in detected]),
        "window_pushes": _summary([float(d.window_pushes or 0) for d in detected]),
        "merged": sum(count - 1 for count in caught_at.values() if count > 1),
    }
    if bootstrap_samples:
        result["detect_hours"]["mean_ci95"] = bootstrap_mean(
            detect_hours, bootstrap_samples
        )
        result["surface_hours"]["mean_ci95"] = bootstrap_mean(
            surface_hours, bootstrap_samples
        )
    return result


def is_feasible(result: dict[str, Any], baseline: dict[str, Any]) -> bool:
    """Whether the schedule caught every regression the baseline caught in the window."""
    return set(baseline["detected_ids"]) <= set(result["detected_ids"])


def objectives(
    result: dict[str, Any],
    cost: Cost = "jobs",
    latency: Latency = "detect",
    statistic: Statistic = "mean",
) -> tuple[float, float]:
    """The two values optuna minimizes: cost and a summary of the latency in hours."""
    value = result[f"{latency}_hours"][statistic]
    return float(result[cost]), float("inf") if value is None else float(value)
