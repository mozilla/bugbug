# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Dataset registrations and readers for the performance alerts pipeline."""

from typing import Any, Iterator

from bugbug import db


def _index_url(index: str, name: str) -> str:
    return (
        "https://community-tc.services.mozilla.com/api/index/v1/task/"
        f"project.bugbug.{index}.latest/artifacts/public/{name}.zst"
    )


# Raw snapshots of the source systems.
PERF_ALERTS_DB = "data/perf_alerts.json"
db.register(PERF_ALERTS_DB, _index_url("data_perf_alerts", "perf_alerts.json"), 1)
PERF_PUSHES_DB = "data/perf_pushes.json"
db.register(PERF_PUSHES_DB, _index_url("data_perf_alerts", "perf_pushes.json"), 1)
PERF_ALERT_BUGS_DB = "data/perf_alert_bugs.json"
db.register(
    PERF_ALERT_BUGS_DB, _index_url("data_perf_alerts", "perf_alert_bugs.json"), 1
)
PERF_TASK_COSTS_DB = "data/perf_task_costs.json"
db.register(
    PERF_TASK_COSTS_DB, _index_url("data_perf_alerts", "perf_task_costs.json"), 1
)

# Derived, labeled datasets.
PERF_REGRESSIONS_DB = "data/perf_regressions.json"
db.register(
    PERF_REGRESSIONS_DB,
    _index_url("data_perf_regressions", "perf_regressions.json"),
    1,
)
PERF_REGRESSION_COMMITS_DB = "data/perf_regression_commits.json"
db.register(
    PERF_REGRESSION_COMMITS_DB,
    _index_url("data_perf_regression_commits", "perf_regression_commits.json"),
    1,
)
PERF_PAIRS_DB = "data/perf_scheduling_pairs.json"
db.register(
    PERF_PAIRS_DB, _index_url("data_perf_regressions", "perf_scheduling_pairs.json"), 1
)
PERF_PUSH_STATUS_DB = "data/perf_push_status.json"
db.register(
    PERF_PUSH_STATUS_DB,
    _index_url("data_perf_regressions", "perf_push_status.json"),
    1,
)


def get_perf_alerts() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_ALERTS_DB)


def get_perf_pushes() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_PUSHES_DB)


def get_perf_alert_bugs() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_ALERT_BUGS_DB)


def get_perf_task_costs() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_TASK_COSTS_DB)


def get_perf_regressions() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_REGRESSIONS_DB)


def get_perf_pairs() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_PAIRS_DB)


def get_perf_push_status() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_PUSH_STATUS_DB)


def get_perf_regression_commits() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_REGRESSION_COMMITS_DB)
