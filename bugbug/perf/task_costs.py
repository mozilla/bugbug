# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Cost helpers joining pushes to the perf task costs snapshot.

The cost unit is machine seconds per worker pool: perf tasks run almost
entirely on Mozilla's own hardware pools, and the snapshot carries no billing
figures. These helpers are for evaluating what a selection strategy would have
spent or saved: ``summarize_push_cost`` prices what actually ran on a push,
while ``build_label_prices`` and ``estimate_labels_cost`` price any set of task
labels, run or not, from each label's typical duration. The ``generate``
command also uses them to check that recent runs have cost data.
"""

import collections
import statistics
from typing import Any, Iterable, Mapping, NamedTuple

from bugbug import db
from bugbug.perf.datasets import PERF_TASK_COSTS_DB
from bugbug.perf.task_labels import get_runnable_identity, normalize_task_label


def load_task_costs(task_ids: set[str] | None = None) -> dict[str, str | None]:
    """Map each task id of the perf task costs snapshot to its worker pool.

    The pool (``task_queue_id``) also names the machine type. Pass ``task_ids``
    to keep only the tasks of interest; the snapshot holds millions of rows, so
    consumers should restrict it to the pushes they need, typically via
    ``collect_push_task_ids``.
    """
    return {
        row["task_id"]: row.get("task_queue_id")
        for row in db.read(PERF_TASK_COSTS_DB)
        if task_ids is None or row["task_id"] in task_ids
    }


def collect_push_task_ids(pushes: Iterable[dict[str, Any]]) -> set[str]:
    """Task ids of every perf job run on the given pushes.

    Retries count separately, since each run has its own task id. The result
    is the filter to pass to ``load_task_costs``.
    """
    return {
        run["task_id"]
        for push in pushes
        for job in push["perf_jobs"]
        for run in job["runs"]
        if run["task_id"]
    }


def summarize_push_cost(
    push: dict[str, Any], costs: Mapping[str, str | None]
) -> dict[str, Any]:
    """Summarize the machine time per worker pool of a push.

    Every perf job run is looked up in ``costs`` (task id to pool, see
    ``load_task_costs``) and its duration added to its pool's tally. The result
    also counts how many runs matched a task, so coverage gaps are visible.
    """
    seconds_by_pool: collections.Counter = collections.Counter()
    matched = 0
    total = 0
    for job in push["perf_jobs"]:
        for run in job["runs"]:
            total += 1
            if not run["task_id"] or run["task_id"] not in costs:
                continue
            matched += 1
            seconds_by_pool[costs[run["task_id"]] or "unknown"] += run["duration"] or 0
    return {
        "runs": total,
        "runs_with_pool": matched,
        "machine_seconds_by_pool": dict(seconds_by_pool),
    }


class LabelPrice(NamedTuple):
    """Typical cost of one run of a task label.

    ``pool`` is the worker pool it usually runs on, ``seconds`` the median run
    duration there, and ``runs`` how many observed runs back the estimate.
    """

    pool: str | None
    seconds: float
    runs: int


def build_label_prices(
    rows: Iterable[dict[str, Any]] | None = None,
) -> dict[str, LabelPrice]:
    """Build a per-label price list from the cost snapshot.

    A task's duration barely depends on the push it ran on, so the median over
    its observed runs prices it on any push, including ones where it was not
    scheduled. Labels are normalized so renamed platforms share one price, and
    the pool is the one most often seen. Reads the snapshot when ``rows`` is
    ``None``.
    """
    if rows is None:
        rows = db.read(PERF_TASK_COSTS_DB)
    durations: dict[str, list[float]] = collections.defaultdict(list)
    pools: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for row in rows:
        runs = row.get("runs") or 0
        if not row.get("label") or not runs or row.get("duration") is None:
            continue
        label = normalize_task_label(row["label"])
        durations[label].extend([row["duration"] / runs] * runs)
        pools[label][row.get("task_queue_id")] += runs
    return {
        label: LabelPrice(
            pools[label].most_common(1)[0][0],
            statistics.median(seconds),
            len(seconds),
        )
        for label, seconds in durations.items()
    }


def estimate_labels_cost(
    labels: Iterable[str], prices: dict[str, LabelPrice]
) -> dict[str, Any]:
    """Estimate the machine seconds per pool of running each label once.

    Labels missing from ``prices``, such as new tests, fall back to the median
    price of labels with the same family and platform family; labels with no
    such siblings are counted as ``unpriced``. Shaped like ``summarize_push_cost`` so the
    two can be compared directly.
    """
    siblings: dict[tuple[str | None, str | None], list[LabelPrice]] = (
        collections.defaultdict(list)
    )
    for known_label, known_price in prices.items():
        identity = get_runnable_identity(known_label)
        siblings[(identity["family"], identity["platform_family"])].append(known_price)

    seconds_by_pool: dict[str, float] = collections.defaultdict(float)
    priced = estimated = unpriced = 0
    for label in labels:
        label = normalize_task_label(label)
        price: LabelPrice | None = prices.get(label)
        if price is not None:
            priced += 1
        else:
            identity = get_runnable_identity(label)
            group = siblings.get((identity["family"], identity["platform_family"]))
            if not group:
                unpriced += 1
                continue
            estimated += 1
            price = LabelPrice(
                collections.Counter(p.pool for p in group).most_common(1)[0][0],
                statistics.median(p.seconds for p in group),
                sum(p.runs for p in group),
            )
        seconds_by_pool[price.pool or "unknown"] += price.seconds
    return {
        "labels": priced + estimated + unpriced,
        "priced": priced,
        "estimated": estimated,
        "unpriced": unpriced,
        "machine_seconds_by_pool": dict(seconds_by_pool),
    }
