# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Extraction from the Treeherder replica, BigQuery ``fxci`` and Bugzilla."""

import logging
from datetime import date, datetime, timedelta
from importlib import resources
from typing import Any, Iterable, Iterator

from bugbug import bugzilla, utils
from bugbug.perf.label_policy import REPOSITORY
from bugbug.redash import RedashClient
from bugbug.utils import utcnow

logger = logging.getLogger(__name__)

TREEHERDER_DATA_SOURCE_ID = 89
BIGQUERY_DATA_SOURCE_ID = 63

RECORD_SEP = "\x1e"
UNIT_SEP = "\x1f"


def load_sql_template(name: str) -> str:
    """Load the SQL template ``bugbug/perf/sql/<name>.sql`` as text."""
    return (resources.files("bugbug.perf.sql") / f"{name}.sql").read_text(
        encoding="utf-8"
    )


def build_alerts_query(created_from: date, created_to: date) -> str:
    """SQL for alerts of summaries created in ``[created_from, created_to)``.

    The templates are filled with ``str.format``, which is safe because every
    value is a constant or a formatted date.
    """
    return load_sql_template("perf_alerts").format(
        repository=REPOSITORY,
        created_from=created_from.isoformat(),
        created_to=created_to.isoformat(),
    )


def build_pushes_query(time_from: datetime, time_to: datetime) -> str:
    """SQL for pushes, their commits and perf jobs, pushed in ``[time_from, time_to)``."""
    return load_sql_template("perf_pushes").format(
        repository=REPOSITORY,
        time_from=time_from.strftime("%Y-%m-%d %H:%M:%S"),
        time_to=time_to.strftime("%Y-%m-%d %H:%M:%S"),
    )


def perf_task_filter() -> str:
    """The predicate selecting the project's performance tasks in ``fxci.tasks``.

    The ``perf_task_filter.sql`` fragment without its comments, shared by the
    cost and capacity queries.
    """
    lines = load_sql_template("perf_task_filter").splitlines()
    return "\n".join(
        line for line in lines if not line.lstrip().startswith("--")
    ).format(repository=REPOSITORY)


def build_task_costs_query(date_from: date, date_to: date) -> str:
    """BigQuery SQL for perf tasks submitted in ``[date_from, date_to)``."""
    return load_sql_template("perf_task_costs").format(
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        perf_task_filter=perf_task_filter(),
    )


def build_capacity_query(
    name: str, date_from: date, date_to: date, **values: str
) -> str:
    """BigQuery SQL of the ``perf_capacity_<name>`` template over ``[date_from, date_to)``.

    ``values`` fill the template's own placeholders: the quoted list of
    ``pools``, the quoted ``build_labels`` and the ``min_labels`` of a full
    suite.
    """
    return load_sql_template(f"perf_capacity_{name}").format(
        repository=REPOSITORY,
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
        perf_task_filter=perf_task_filter(),
        **values,
    )


def _split_agg(value: str | None) -> list[str]:
    """Split a pipe-joined ``string_agg`` column into a list, empty for NULL."""
    return [part for part in value.split("|") if part] if value else []


def normalize_alert_row(row: dict[str, Any]) -> dict[str, Any]:
    """Turn a raw alerts query row into a stored alert record.

    Only splits the aggregated tag and task columns into lists. Anything
    computed from policy tables, such as status names and normalized task
    labels, is added when reading, by
    :func:`bugbug.perf.regression_labeling.derive_alert_fields`, so the archive
    keeps source data only.
    """
    alert = dict(row)
    alert["summary_tags"] = _split_agg(row.get("summary_tags"))
    alert["task_labels"] = _split_agg(row.get("task_labels"))
    alert["task_ids"] = _split_agg(row.get("task_ids"))
    return alert


def parse_push_row(row: dict[str, Any]) -> dict[str, Any]:
    """Unpack a raw pushes query row into a push record with nested lists.

    ``commits`` and ``perf_jobs`` arrive as strings packed with the ASCII
    record (0x1e) and unit (0x1f) separators used by the SQL template. Commits
    become ``{revision, desc}``, their bug id and backout flag being derived
    when read (``datasets.derive_push_fields``); job runs are grouped by label
    into ``{label, tier, duration, runs[]}``.
    """
    commits = []
    for record in (row.get("commits") or "").split(RECORD_SEP):
        if not record:
            continue
        revision, _, desc = record.partition(UNIT_SEP)
        commits.append({"revision": revision, "desc": desc})

    jobs: dict[str, dict[str, Any]] = {}
    for record in (row.get("perf_jobs") or "").split(RECORD_SEP):
        if not record:
            continue
        label, result, duration, tier, task_id, retry_id = (
            record.split(UNIT_SEP) + [""] * 5
        )[:6]
        job = jobs.setdefault(
            label, {"label": label, "duration": 0, "tier": None, "runs": []}
        )
        job["duration"] += int(duration) if duration else 0
        if tier:
            job["tier"] = int(tier)
        job["runs"].append(
            {
                "task_id": task_id or None,
                "retry_id": int(retry_id) if retry_id else None,
                "result": result,
                "duration": int(duration) if duration else None,
            }
        )

    return {
        "push_id": row["push_id"],
        "repository": row["repository"],
        "revision": row["revision"],
        "time": row["push_time"],
        "author": row.get("author"),
        "commits": commits,
        "perf_jobs": sorted(jobs.values(), key=lambda job: job["label"]),
    }


def fetch_alerts(
    client: RedashClient, created_from: date, window_days: int = 30
) -> Iterator[dict[str, Any]]:
    """Yield normalized alert rows from ``created_from`` to today.

    The range is queried in ``window_days`` slices so no single Redash result
    grows large.
    """
    created_to = utcnow().date() + timedelta(days=1)
    window_start = created_from
    while window_start < created_to:
        window_end = min(window_start + timedelta(days=window_days), created_to)
        rows = client.execute(
            TREEHERDER_DATA_SOURCE_ID,
            build_alerts_query(window_start, window_end),
        )
        logger.info(
            "Retrieved %d alert rows created between %s and %s",
            len(rows),
            window_start,
            window_end,
        )
        for row in rows:
            yield normalize_alert_row(row)
        window_start = window_end


def merge_alerts(
    previous: Iterable[dict[str, Any]], fetched: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge a fresh alerts fetch into the previous snapshot.

    Fetched rows replace previous ones with the same ``alert_id``, except that
    task labels captured earlier are kept when the new row has none, since
    Treeherder drops the job behind a datapoint after 120 days. Rows the
    source no longer returns are kept, so the snapshot outlives Perfherder's
    own retention and only ever grows.
    """
    merged = {alert["alert_id"]: alert for alert in previous}
    for alert in fetched:
        old = merged.get(alert["alert_id"])
        if old is not None and old["task_labels"] and not alert["task_labels"]:
            for field in ("task_labels", "task_ids"):
                alert[field] = old[field]
        merged[alert["alert_id"]] = alert
    return sorted(
        merged.values(), key=lambda alert: (alert["summary_created"], alert["alert_id"])
    )


def _count_runs(push: dict[str, Any]) -> int:
    return sum(len(job["runs"]) for job in push.get("perf_jobs", []))


def merge_push(
    previous: dict[str, Any] | None, fetched: dict[str, Any]
) -> dict[str, Any]:
    """Refresh a stored push with its refetched row.

    Fetched fields win, except that the stored ``perf_jobs`` stay when they
    hold more runs than the fetched ones: Treeherder deletes jobs after 120
    days, so a refetch after a long gap returns the push without them. Fields
    the fetch does not produce are kept as well.
    """
    if previous is None:
        return fetched
    merged = {**previous, **fetched}
    if _count_runs(previous) > _count_runs(fetched):
        merged["perf_jobs"] = previous["perf_jobs"]
    return merged


def fetch_pushes(
    client: RedashClient,
    time_from: datetime,
    time_to: datetime,
    window_days: int = 3,
) -> Iterator[dict[str, Any]]:
    """Yield parsed push records for ``[time_from, time_to)`` in ``window_days`` slices."""
    window_start = time_from
    while window_start < time_to:
        window_end = min(window_start + timedelta(days=window_days), time_to)
        rows = client.execute(
            TREEHERDER_DATA_SOURCE_ID,
            build_pushes_query(window_start, window_end),
        )
        logger.info(
            "Retrieved %d pushes between %s and %s", len(rows), window_start, window_end
        )
        for row in rows:
            yield parse_push_row(row)
        window_start = window_end


HG_REPOSITORY_URL = f"https://hg.mozilla.org/integration/{REPOSITORY}"
PUSHLOG_URL = f"{HG_REPOSITORY_URL}/json-pushes"
# The pushlog filters on its own push dates, which are the push times
# Treeherder stores; the margin covers rounding at the window edges.
PUSHLOG_MARGIN = timedelta(hours=1)


def pushlog_windows(
    times: Iterable[datetime], window_days: int
) -> list[tuple[datetime, datetime]]:
    """Group times into ``[start, end)`` windows of at most ``window_days``.

    Each window starts at the first time it covers and spans of time without
    any are skipped, so filling the pushlog ids of a few pushes spread over a
    year costs one request per cluster rather than one per window of the year.
    """
    windows: list[tuple[datetime, datetime]] = []
    for time in sorted(times):
        if windows and time < windows[-1][1]:
            continue
        windows.append((time, time + timedelta(days=window_days)))
    return windows


def fetch_pushlog_ids(windows: Iterable[tuple[datetime, datetime]]) -> dict[str, int]:
    """Map the head changeset of each push in the windows to its pushlog id.

    Treeherder pushes have no pushlog id, which the taskgraph's push-count
    rules are defined on, so it comes from hg.mozilla.org's pushlog, queried
    by push date and joined on the head changeset.
    """
    session = utils.get_session("hg.mozilla.org")
    pushlog_ids: dict[str, int] = {}
    for window_start, window_end in windows:
        response = session.get(
            PUSHLOG_URL,
            params={
                "version": "2",
                "tipsonly": "1",
                "startdate": (window_start - PUSHLOG_MARGIN).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "enddate": (window_end + PUSHLOG_MARGIN).strftime("%Y-%m-%d %H:%M:%S"),
            },
            timeout=120,
        )
        response.raise_for_status()
        pushes = response.json()["pushes"]
        for pushlog_id, push in pushes.items():
            if push["changesets"]:
                pushlog_ids[push["changesets"][-1]] = int(pushlog_id)
        logger.info(
            "Retrieved %d pushlog ids between %s and %s",
            len(pushes),
            window_start,
            window_end,
        )
    return pushlog_ids


def set_pushlog_ids(
    pushes: Iterable[dict[str, Any]], pushlog_ids: dict[str, int]
) -> int:
    """Fill ``pushlog_id`` on the pushes that lack one; returns how many still do."""
    missing = 0
    for push in pushes:
        if push.get("pushlog_id") is not None:
            continue
        push["pushlog_id"] = pushlog_ids.get(push["revision"])
        if push["pushlog_id"] is None:
            missing += 1
    return missing


COST_FIELDS = (
    "task_id",
    "label",
    "project",
    "task_queue_id",
    "submission_date",
    "runs",
    "duration",
)


def normalize_cost_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep only the source columns; identities are derived at read time."""
    return {field: row.get(field) for field in COST_FIELDS}


def fetch_task_costs(
    client: RedashClient,
    date_from: date,
    date_to: date,
    window_days: int = 2,
) -> Iterator[dict[str, Any]]:
    """Yield perf task cost rows for ``[date_from, date_to)`` in ``window_days`` slices."""
    window_start = date_from
    while window_start < date_to:
        window_end = min(window_start + timedelta(days=window_days), date_to)
        rows = client.execute(
            BIGQUERY_DATA_SOURCE_ID,
            build_task_costs_query(window_start, window_end),
        )
        logger.info(
            "Retrieved %d perf task cost rows between %s and %s",
            len(rows),
            window_start,
            window_end,
        )
        for row in rows:
            yield normalize_cost_row(row)
        window_start = window_end


BUG_FIELDS = (
    "id",
    "summary",
    "status",
    "resolution",
    "product",
    "component",
    "keywords",
    "creation_time",
    "last_change_time",
    "regressed_by",
    "regressions",
    "dupe_of",
    "depends_on",
    "blocks",
)


def fetch_bugs(
    bug_ids: Iterable[int], follow_duplicates: int = 3
) -> list[dict[str, Any]]:
    """Fetch ``BUG_FIELDS`` of the given public bugs from Bugzilla.

    Bugs resolved as duplicates point at a canonical bug through ``dupe_of``;
    those targets are fetched too, up to ``follow_duplicates`` hops, so the
    canonical resolution and ``regressed_by`` are available for labeling. The
    result is published and the Bugzilla token, when one is configured, may
    see restricted bugs, so any bug in a Bugzilla group is dropped and its
    ``dupe_of`` is never followed.
    """
    bugs: dict[int, dict[str, Any]] = {}
    requested: set[int] = set()
    restricted = 0
    pending = sorted(set(bug_ids))
    for _ in range(follow_duplicates + 1):
        requested.update(pending)
        for bug in bugzilla.get_fields(pending, BUG_FIELDS + ("groups",)).values():
            if bug.pop("groups"):
                restricted += 1
                continue
            bugs[bug["id"]] = bug
        pending = sorted(
            {bug["dupe_of"] for bug in bugs.values() if bug.get("dupe_of")} - requested
        )
        if not pending:
            break
    logger.info(
        "Retrieved %d public bugs for %d linked bug ids, dropped %d restricted",
        len(bugs),
        len(set(bug_ids)),
        restricted,
    )
    return list(bugs.values())
