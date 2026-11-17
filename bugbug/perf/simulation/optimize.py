# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tuning strategy parameters with optuna and reporting against production.

Each family gets one multi-objective study on the optimize window, minimizing
cost and mean latency at once under the constraint that every regression the
production baseline has the latency for, the strategy has it for too. The
Pareto front is then evaluated on the eval window, which the tuning never saw.
"""

import logging
from typing import Any

from bugbug.perf.simulation.capacity import CiSettings, simulate_ci
from bugbug.perf.simulation.detection import detect, replay
from bugbug.perf.simulation.metrics import (
    Cost,
    Latency,
    Statistic,
    Window,
    evaluate,
    missed_ids,
    objectives,
)
from bugbug.perf.simulation.strategies import FAMILIES, Strategy
from bugbug.perf.simulation.timeline import Timeline

logger = logging.getLogger(__name__)

# NSGA-II breeds each generation from the previous one, so with a few hundred
# trials a small population gives it many generations to evolve; optuna's
# default of 50 would spend most of them on the random first generation.
NSGA_POPULATION = 20


def run(
    strategy: Strategy,
    timeline: Timeline,
    windows: list[Window],
    bootstrap_samples: int = 0,
    ci: CiSettings | None = None,
) -> list[dict[str, Any]]:
    """Replay ``strategy`` once and evaluate it on each window, through CI when ``ci`` is given."""
    schedule = replay(strategy, timeline)
    detections = detect(schedule, timeline)
    ci_result = simulate_ci(schedule, timeline, detections, ci) if ci else None
    return [
        evaluate(timeline, schedule, detections, window, bootstrap_samples, ci_result)
        for window in windows
    ]


def optimize_family(
    family: str,
    timeline: Timeline,
    window: Window,
    baseline: dict[str, Any],
    n_trials: int,
    seed: int = 0,
    cost: Cost = "jobs",
    latency: Latency = "detect",
    statistic: Statistic = "mean",
    ci: CiSettings | None = None,
    max_cost_ratio: float | None = None,
    **fixed_params: Any,
) -> Any:
    """Run one study for ``family`` on ``window`` and return it.

    The search space comes from ``search_space_for`` over the window's pushes.
    A one-parameter family is swept over every value of ``sweep``: all the
    integers of an integer parameter, ``n_trials`` evenly or log-evenly spaced
    values of a real one. The others use NSGA-II with ``NSGA_POPULATION`` for
    ``n_trials`` trials. Trials lacking the ``latency`` of a regression the
    baseline has it for are pruned, so they never reach the front; see
    ``pruned_trials``.
    ``fixed_params`` are passed to every strategy as they are, outside the
    search. The space used is kept in the study's ``search_space`` attribute.
    With ``ci``, trials whose plain job count exceeds ``max_cost_ratio`` times
    the baseline's are pruned before the CI replay, which is what makes a
    dense schedule expensive to evaluate.
    """
    import optuna

    cls = FAMILIES[family]
    space = cls.search_space_for(
        push for push in timeline.pushes if window.contains(push.time)
    )

    def objective(trial: Any) -> tuple[float, float]:
        params = {**cls.suggest(trial, space), **fixed_params}
        strategy = cls(**params)
        if ci is not None and max_cost_ratio is not None:
            plain_cost = "jobs" if cost == "total_jobs" else cost
            plain = run(strategy, timeline, [window])[0]
            if plain[plain_cost] > max_cost_ratio * baseline[plain_cost]:
                trial.set_user_attr("metrics", plain)
                trial.set_user_attr("pruned_for", "cost")
                raise optuna.TrialPruned()
        result = run(strategy, timeline, [window], ci=ci)[0]
        trial.set_user_attr("metrics", result)
        missed = missed_ids(result, baseline, latency)
        if missed:
            trial.set_user_attr("missed_ids", missed)
            raise optuna.TrialPruned()
        return objectives(result, cost, latency, statistic)

    values = cls.sweep(space, n_trials)
    if values is not None:
        sampler: Any = optuna.samplers.GridSampler(values, seed=seed)
        n_trials = sum(len(points) for points in values.values())
    else:
        sampler = optuna.samplers.NSGAIISampler(
            population_size=NSGA_POPULATION, seed=seed
        )
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(
        directions=["minimize", "minimize"], sampler=sampler, study_name=family
    )
    study.set_user_attr(
        "search_space", {name: list(bounds) for name, bounds in space.items()}
    )
    study.optimize(objective, n_trials=n_trials)
    logger.info(
        "%s: %d trials, %d on the Pareto front",
        family,
        len(study.trials),
        len(study.best_trials),
    )
    return study


def pruned_trials(study: Any) -> list[dict[str, Any]]:
    """Pruned trials: the regressions whose latency they lack, or ``"cost"``.

    A batching family runs the whole suite on every batch, so it can only
    lack a regression's latency when too few batches follow it before the
    pushes end: the strategy went longer without a batch than the data can
    judge, which says nothing about its correctness. For a selection family,
    a miss is a real gap. With the CI model, trials over ``max_cost_ratio``
    are pruned before the replay and carry ``pruned_for`` set to ``cost``.
    """
    import optuna

    return [
        {
            "params": trial.params,
            "missed_ids": trial.user_attrs.get("missed_ids", []),
            "pruned_for": trial.user_attrs.get("pruned_for", "missed"),
        }
        for trial in study.trials
        if trial.state == optuna.trial.TrialState.PRUNED
    ]


def pareto_points(study: Any) -> list[dict[str, Any]]:
    """The front's trials as ``{params, objectives, metrics}``, cheapest first."""
    points = [
        {
            "params": trial.params,
            "objectives": list(trial.values),
            "metrics": trial.user_attrs.get("metrics"),
        }
        for trial in study.best_trials
    ]
    return sorted(points, key=lambda point: tuple(point["objectives"]))


def evaluate_points(
    family: str,
    points: list[dict[str, Any]],
    timeline: Timeline,
    window: Window,
    cost: Cost = "jobs",
    latency: Latency = "detect",
    bootstrap_samples: int = 0,
    statistic: Statistic = "mean",
    ci: CiSettings | None = None,
    **fixed_params: Any,
) -> list[dict[str, Any]]:
    """Re-run each Pareto point on another window, keeping its optimize-window values."""
    rows = []
    for point in points:
        strategy = FAMILIES[family](**{**point["params"], **fixed_params})
        result = run(strategy, timeline, [window], bootstrap_samples, ci)[0]
        rows.append(
            {
                "family": family,
                "name": strategy.name,
                "params": point["params"],
                "optimize": {"objectives": point["objectives"]},
                "eval": {
                    "objectives": list(objectives(result, cost, latency, statistic)),
                    "metrics": result,
                },
            }
        )
    return rows


def matching_points(
    rows: list[dict[str, Any]], baseline_objectives: tuple[float, float]
) -> dict[str, dict[str, Any] | None]:
    """The front's rows that answer the two usual questions against production.

    Chosen on the optimize window, where the baseline's objectives were
    measured too, so the eval window only ever judges a choice already made:
    ``at_cost`` is the fastest row that costs no more than production and
    ``at_latency`` the cheapest row that is no slower.
    """
    cost, latency = baseline_objectives
    cheaper = [row for row in rows if row["optimize"]["objectives"][0] <= cost]
    faster = [row for row in rows if row["optimize"]["objectives"][1] <= latency]
    return {
        "at_cost": min(
            cheaper, key=lambda r: r["optimize"]["objectives"][1], default=None
        ),
        "at_latency": min(
            faster, key=lambda r: r["optimize"]["objectives"][0], default=None
        ),
    }
