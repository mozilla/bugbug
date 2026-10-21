# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Cost helpers joining pushes to the perf task costs snapshot.

Perf tasks mostly run on Mozilla's own hardware pools, which have no dollar
cost in ``fxci``, so the usable unit is machine seconds per worker pool, with
dollars added only for the few tasks that run in the cloud. These helpers are
for evaluating what a selection strategy would have spent or saved: ``summarize_push_cost``
prices what actually ran on a push, while ``build_label_prices`` and
``estimate_labels_cost`` price any set of task labels, run or not, from each label's
typical duration. The ``generate`` command also uses them to check that recent
runs have cost data.
"""

import collections
import statistics
from typing import Any, Iterable, NamedTuple

from bugbug import db
from bugbug.perf.datasets import PERF_TASK_COSTS_DB
from bugbug.perf.task_labels import get_runnable_identity, normalize_task_label


class TaskCost(NamedTuple):
    """Compact cost record for one task.

    ``pool`` is the worker pool (``task_queue_id``), which also names the
    machine type; ``cost`` is the dollar figure, ``None`` off the cloud; and
    ``costed_runs`` says how many runs that figure covers.
    """

    pool: str | None
    cost: float | None
    costed_runs: int | None


def load_task_costs(task_ids: set[str] | None = None) -> dict[str, TaskCost]:
    """Index the perf task costs snapshot by Taskcluster task id.

    Each row is reduced to a :class:`TaskCost`. Pass ``task_ids`` to keep only
    the tasks of interest; the snapshot holds millions of rows, so consumers
    should restrict it to the pushes they need, typically via ``collect_push_task_ids``.
    """
    return {
        row["task_id"]: TaskCost(
            row.get("task_queue_id"), row.get("cost"), row.get("costed_runs")
        )
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
    push: dict[str, Any], costs: dict[str, TaskCost]
) -> dict[str, Any]:
    """Summarize machine time per worker pool and known cloud cost of a push.

    Every perf job run is looked up in ``costs``; its duration is added to its
    pool's tally, and its per-run share of the task's dollar cost is added to
    the total when one exists. The result also counts how many runs matched a
    pool and how many were priced, so coverage gaps are visible.
    """
    seconds_by_pool: collections.Counter = collections.Counter()
    cost_total = 0.0
    costed = 0
    matched = 0
    total = 0
    for job in push["perf_jobs"]:
        for run in job["runs"]:
            total += 1
            cost = costs.get(run["task_id"]) if run["task_id"] else None
            if cost is None:
                continue
            matched += 1
            seconds_by_pool[cost.pool or "unknown"] += run["duration"] or 0
            if cost.cost is not None and cost.costed_runs:
                cost_total += cost.cost / cost.costed_runs
                costed += 1
    return {
        "runs": total,
        "runs_with_pool": matched,
        "runs_with_cost": costed,
        "machine_seconds_by_pool": dict(seconds_by_pool),
        "cost": cost_total if costed else None,
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
