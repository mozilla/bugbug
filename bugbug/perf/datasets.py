# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Dataset registrations and readers for the performance alerts pipeline."""

import logging
import os
from typing import Any, Iterator
from urllib.parse import urljoin

import requests

from bugbug import db, utils
from bugbug.perf.label_policy import REPOSITORY

logger = logging.getLogger(__name__)


def _index_url(index: str, name: str) -> str:
    return (
        "https://community-tc.services.mozilla.com/api/index/v1/task/"
        f"project.bugbug.{index}.latest/artifacts/public/{name}.zst"
    )


# Raw snapshots of the source systems.
#
# The alerts, pushes and task costs snapshots are archives: each run merges new
# data into the previous snapshot, and they are the only copy of what the
# sources have since deleted, such as alerts older than a year and jobs older
# than 120 days. They can never be rebuilt from scratch, so their format changes
# by these rules:
#
# - Store what the source returned. Fields computed from bugbug's rules are
#   derived when reading, as derive_alert_fields does, so editing a rule never
#   changes the format.
# - Adding a field needs no version bump. Readers must treat it as optional,
#   because archived rows that the source no longer returns never get it.
# - Renaming or removing a field, or changing what it means, needs a version
#   bump and a converter. load_previous_snapshot refuses a published snapshot
#   whose version differs, so the run fails instead of starting over. Write the
#   converter there: when the published version is the previous one, download
#   the file anyway (db.download refuses it, utils.download_check_etag does
#   not), rewrite each record into the new format, write it to ``path`` and
#   return True, so the retrieval merges into the converted snapshot.
#
# The bugs snapshot is refetched whole every run, so it is not an archive.
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


def load_previous_snapshot(path: str) -> bool:
    """Make the previous snapshot of an archive available locally.

    Returns True when ``path`` holds it, and False only on the first run, when
    the Taskcluster index has never published it. Every other failure raises:
    publishing a snapshot rebuilt from what the sources still return would drop
    everything they have deleted. When the published snapshot cannot be
    fetched, a local copy is used if there is one, as in local runs and reruns.
    """
    name = os.path.basename(path)
    have_local = db.exists(path)

    def unavailable(reason: str) -> bool:
        if have_local:
            logger.warning("%s, using the local copy", reason)
            return True
        raise RuntimeError(
            f"{reason}. Not starting {name} over, which would drop the data the "
            "sources have deleted; rerun once the snapshot can be fetched."
        )

    version_url = urljoin(db.DATABASES[path]["url"], f"{name}.version")
    try:
        response = utils.get_session("community-tc").get(
            version_url, headers={"User-Agent": utils.get_user_agent()}, timeout=60
        )
    except requests.exceptions.RequestException as e:
        return unavailable(f"Could not fetch the published {name}: {e}")

    if response.status_code == 404:
        if have_local:
            logger.warning("%s has never been published, using the local copy", name)
            return True
        logger.warning("%s has never been published, starting a new archive", name)
        return False
    if not response.ok:
        return unavailable(
            f"Could not fetch the published {name}: HTTP {response.status_code}"
        )

    try:
        published = int(response.text)
    except ValueError:
        return unavailable(f"Unreadable version for the published {name}")
    expected = db.DATABASES[path]["version"]
    if published != expected:
        raise RuntimeError(
            f"{name} is published as version {published}, but this code writes "
            f"version {expected}. The archive cannot be rebuilt from the sources, "
            "so it needs a converter: see the notes above the archive "
            "registrations in bugbug/perf/datasets.py."
        )

    try:
        downloaded = db.download(path)
    except requests.exceptions.RequestException as e:
        return unavailable(f"Could not download the published {name}: {e}")
    if downloaded and db.exists(path):
        return True
    return unavailable(f"Could not download the published {name}")


# The archive readers skip rows of other repositories, which snapshots
# retrieved before the pipeline became autoland-only can still hold. Labeling
# relies on each commit landing in a single push, and a mozilla-central merge
# push would list autoland culprits again as clean commits. The next retrieval
# drops those rows from the archives.


def get_perf_alerts() -> Iterator[dict[str, Any]]:
    for alert in db.read(PERF_ALERTS_DB):
        if alert["repository"] == REPOSITORY:
            yield alert


def get_perf_pushes() -> Iterator[dict[str, Any]]:
    for push in db.read(PERF_PUSHES_DB):
        if push["repository"] == REPOSITORY:
            yield push


def get_perf_alert_bugs() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_ALERT_BUGS_DB)


def get_perf_task_costs() -> Iterator[dict[str, Any]]:
    for row in db.read(PERF_TASK_COSTS_DB):
        if row["project"] == REPOSITORY:
            yield row


def get_perf_regressions() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_REGRESSIONS_DB)


def get_perf_pairs() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_PAIRS_DB)


def get_perf_push_status() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_PUSH_STATUS_DB)


def get_perf_regression_commits() -> Iterator[dict[str, Any]]:
    yield from db.read(PERF_REGRESSION_COMMITS_DB)
