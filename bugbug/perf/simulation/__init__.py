# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Replay of performance test scheduling strategies over autoland history.

Built on the performance alerts datasets: pushes, confirmed regressions and
commit risk scores. ``timeline`` loads them, ``strategies`` takes the
scheduling decisions, ``detection`` finds when each regression is caught,
``metrics`` measures cost and latency over a window, ``splits`` cuts the
history into optimize and eval windows and ``optimize`` tunes parameters with
optuna. See docs/perf-scheduling-simulation.md.
"""

from bugbug.perf.simulation.capacity import (
    CapacityModel,
    CiResult,
    CiSettings,
    LabelRuntime,
    PoolCapacity,
    calibrate_scales,
    fetch_capacity,
    simulate_ci,
)
from bugbug.perf.simulation.detection import (
    FORE_WINDOW,
    Detection,
    Schedule,
    detect,
    replay,
)
from bugbug.perf.simulation.metrics import (
    Window,
    evaluate,
    is_feasible,
    latency_key,
    latency_label,
    missed_ids,
    objectives,
)
from bugbug.perf.simulation.optimize import (
    evaluate_points,
    matching_points,
    optimize_family,
    pareto_points,
    pruned_trials,
    run,
)
from bugbug.perf.simulation.splits import default_bounds, make_windows
from bugbug.perf.simulation.stats import bootstrap_mean, percentile
from bugbug.perf.simulation.strategies import (
    ALL,
    FAMILIES,
    PRESETS,
    RISK_FAMILIES,
    CommitCount,
    FixedInterval,
    Production,
    Rapb,
    RapbLa,
    RapbLaPushes,
    RapbPushes,
    Rasb,
    RasbLa,
    RecordedProduction,
    RiskThreshold,
    Strategy,
    TimeSinceBatch,
    make_strategy,
    parse_strategy,
)
from bugbug.perf.simulation.timeline import (
    FULL_SUITE_MIN_LABELS,
    Commit,
    Push,
    Regression,
    Timeline,
    load_risks,
)

__all__ = [
    "ALL",
    "CapacityModel",
    "CiResult",
    "CiSettings",
    "LabelRuntime",
    "PoolCapacity",
    "FAMILIES",
    "FORE_WINDOW",
    "FULL_SUITE_MIN_LABELS",
    "PRESETS",
    "RISK_FAMILIES",
    "Commit",
    "CommitCount",
    "Detection",
    "FixedInterval",
    "Production",
    "Push",
    "Rapb",
    "RapbLa",
    "RapbLaPushes",
    "RapbPushes",
    "Rasb",
    "RasbLa",
    "RecordedProduction",
    "Regression",
    "RiskThreshold",
    "Schedule",
    "Strategy",
    "TimeSinceBatch",
    "Timeline",
    "Window",
    "bootstrap_mean",
    "calibrate_scales",
    "default_bounds",
    "detect",
    "evaluate",
    "evaluate_points",
    "fetch_capacity",
    "is_feasible",
    "latency_key",
    "latency_label",
    "load_risks",
    "make_strategy",
    "make_windows",
    "matching_points",
    "missed_ids",
    "objectives",
    "optimize_family",
    "pareto_points",
    "pruned_trials",
    "parse_strategy",
    "percentile",
    "replay",
    "run",
    "simulate_ci",
]
