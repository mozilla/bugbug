# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Extraction from the Treeherder replica, BigQuery ``fxci`` and Bugzilla."""

import logging
import re
from datetime import date, datetime, timedelta
from importlib import resources
from typing import Any, Iterable, Iterator

from bugbug import utils
from bugbug.perf.label_policy import ALERT_STATUS, SUMMARY_STATUS
from bugbug.perf.task_labels import normalize_task_label
from bugbug.redash import RedashClient
from bugbug.utils import utcnow

logger = logging.getLogger(__name__)

TREEHERDER_DATA_SOURCE_ID = 89
BIGQUERY_DATA_SOURCE_ID = 63
BUGZILLA_REST_URL = "https://bugzilla.mozilla.org/rest/bug"

RECORD_SEP = "\x1e"
UNIT_SEP = "\x1f"

BUG_RE = re.compile(r"\b[Bb]ug\s+(\d+)")
BACKOUT_RE = re.compile(r"^\s*(backed\s*out|backout|revert)", re.IGNORECASE)
SAFE_SQL_VALUE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def load_sql_template(name: str) -> str:
    """Load the SQL template ``bugbug/perf/sql/<name>.sql`` as text."""
    return (resources.files("bugbug.perf.sql") / f"{name}.sql").read_text(
        encoding="utf-8"
    )


def _sql_string_list(values: Iterable[str]) -> str:
    """Render values as a quoted SQL ``IN`` list, refusing anything but plain names.

    Templates are filled with ``str.format``, so every value that reaches the
    SQL is checked against ``SAFE_SQL_VALUE_RE`` to rule out injection.
    """
    quoted = []
    for value in values:
        if not SAFE_SQL_VALUE_RE.match(value):
            raise ValueError(f"Unsafe SQL value: {value!r}")
        quoted.append(f"'{value}'")
    return ", ".join(quoted)


def build_alerts_query(
    repositories: Iterable[str], created_from: date, created_to: date
) -> str:
    """SQL for alerts of summaries created in ``[created_from, created_to)``."""
    return load_sql_template("perf_alerts").format(
        repositories=_sql_string_list(repositories),
        created_from=created_from.isoformat(),
        created_to=created_to.isoformat(),
    )


def build_pushes_query(
    repositories: Iterable[str], time_from: datetime, time_to: datetime
) -> str:
    """SQL for pushes, their commits and perf jobs, pushed in ``[time_from, time_to)``."""
    return load_sql_template("perf_pushes").format(
        repositories=_sql_string_list(repositories),
        time_from=time_from.strftime("%Y-%m-%d %H:%M:%S"),
        time_to=time_to.strftime("%Y-%m-%d %H:%M:%S"),
    )


def build_task_costs_query(
    repositories: Iterable[str], date_from: date, date_to: date
) -> str:
    """BigQuery SQL for perf tasks submitted in ``[date_from, date_to)``."""
    return load_sql_template("perf_task_costs").format(
        repositories=_sql_string_list(repositories),
        date_from=date_from.isoformat(),
        date_to=date_to.isoformat(),
    )


def _split_agg(value: str | None) -> list[str]:
    """Split a pipe-joined ``string_agg`` column into a list, empty for NULL."""
    return [part for part in value.split("|") if part] if value else []


def normalize_alert_row(row: dict[str, Any]) -> dict[str, Any]:
    """Turn a raw alerts query row into a stored alert record.

    Adds human-readable status names, splits the aggregated tag and task
    columns into lists, and attaches the normalized task labels.
    """
    alert = dict(row)
    alert["summary_status_name"] = SUMMARY_STATUS.get(row["summary_status"], "unknown")
    alert["alert_status_name"] = ALERT_STATUS.get(row["alert_status"], "unknown")
    alert["summary_tags"] = _split_agg(row.get("summary_tags"))
    alert["task_labels"] = _split_agg(row.get("task_labels"))
    alert["task_ids"] = _split_agg(row.get("task_ids"))
    alert["normalized_labels"] = sorted(
        {normalize_task_label(label) for label in alert["task_labels"]}
    )
    return alert


def parse_push_row(row: dict[str, Any]) -> dict[str, Any]:
    """Unpack a raw pushes query row into a push record with nested lists.

    ``commits`` and ``perf_jobs`` arrive as strings packed with the ASCII
    record (0x1e) and unit (0x1f) separators used by the SQL template. Commits
    become ``{revision, desc, bug_id, backout}``; job runs are grouped by label
    into ``{label, tier, duration, runs[]}``.
    """
    commits = []
    for record in (row.get("commits") or "").split(RECORD_SEP):
        if not record:
            continue
        revision, _, desc = record.partition(UNIT_SEP)
        bug_match = BUG_RE.search(desc)
        commits.append(
            {
                "revision": revision,
                "desc": desc,
                "bug_id": int(bug_match.group(1)) if bug_match else None,
                "backout": BACKOUT_RE.match(desc) is not None,
            }
        )

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
    client: RedashClient,
    repositories: Iterable[str],
    created_from: date,
    window_days: int = 30,
) -> Iterator[dict[str, Any]]:
    """Yield normalized alert rows from ``created_from`` to today.

    The range is queried in ``window_days`` slices so no single Redash result
    grows large.
    """
    repositories = tuple(repositories)
    created_to = utcnow().date() + timedelta(days=1)
    window_start = created_from
    while window_start < created_to:
        window_end = min(window_start + timedelta(days=window_days), created_to)
        rows = client.execute(
            TREEHERDER_DATA_SOURCE_ID,
            build_alerts_query(repositories, window_start, window_end),
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
            for field in ("task_labels", "task_ids", "normalized_labels"):
                alert[field] = old[field]
        merged[alert["alert_id"]] = alert
    return sorted(
        merged.values(), key=lambda alert: (alert["summary_created"], alert["alert_id"])
    )


def fetch_pushes(
    client: RedashClient,
    repositories: Iterable[str],
    time_from: datetime,
    time_to: datetime,
    window_days: int = 3,
) -> Iterator[dict[str, Any]]:
    """Yield parsed push records for ``[time_from, time_to)`` in ``window_days`` slices."""
    repositories = tuple(repositories)
    window_start = time_from
    while window_start < time_to:
        window_end = min(window_start + timedelta(days=window_days), time_to)
        rows = client.execute(
            TREEHERDER_DATA_SOURCE_ID,
            build_pushes_query(repositories, window_start, window_end),
        )
        logger.info(
            "Retrieved %d pushes between %s and %s", len(rows), window_start, window_end
        )
        for row in rows:
            yield parse_push_row(row)
        window_start = window_end


COST_FIELDS = (
    "task_id",
    "label",
    "project",
    "task_queue_id",
    "submission_date",
    "runs",
    "duration",
    "cost",
    "costed_runs",
    "cloud_provider",
)


def normalize_cost_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep only the source columns; identities are derived at read time."""
    return {field: row.get(field) for field in COST_FIELDS}


def fetch_task_costs(
    client: RedashClient,
    repositories: Iterable[str],
    date_from: date,
    date_to: date,
    window_days: int = 2,
) -> Iterator[dict[str, Any]]:
    """Yield perf task cost rows for ``[date_from, date_to)`` in ``window_days`` slices."""
    repositories = tuple(repositories)
    window_start = date_from
    while window_start < date_to:
        window_end = min(window_start + timedelta(days=window_days), date_to)
        rows = client.execute(
            BIGQUERY_DATA_SOURCE_ID,
            build_task_costs_query(repositories, window_start, window_end),
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
    bug_ids: Iterable[int], batch_size: int = 100, follow_duplicates: int = 3
) -> list[dict[str, Any]]:
    """Fetch ``BUG_FIELDS`` for the given bugs from the Bugzilla REST API.

    Bugs resolved as duplicates point at a canonical bug through ``dupe_of``;
    those targets are fetched too, up to ``follow_duplicates`` hops, so the
    canonical resolution and ``regressed_by`` are available for labeling.
    Public bugs need no credentials; ``BUGBUG_BUGZILLA_TOKEN`` is sent when
    available to lift rate limits.
    """
    session = utils.get_session("bugzilla")
    token = utils.get_secret("BUGZILLA_TOKEN", default_value="")
    if token:
        session.headers["X-Bugzilla-API-Key"] = token

    bugs: dict[int, dict[str, Any]] = {}
    pending = sorted(set(bug_ids))
    for _ in range(follow_duplicates + 1):
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            response = session.get(
                BUGZILLA_REST_URL,
                params={
                    "id": ",".join(str(bug_id) for bug_id in batch),
                    "include_fields": ",".join(BUG_FIELDS),
                },
                timeout=120,
            )
            response.raise_for_status()
            for bug in response.json()["bugs"]:
                bugs[bug["id"]] = bug
        pending = sorted(
            {bug["dupe_of"] for bug in bugs.values() if bug.get("dupe_of")} - set(bugs)
        )
        if not pending:
            break
    logger.info("Retrieved %d bugs for %d linked bug ids", len(bugs), len(set(bug_ids)))
    return list(bugs.values())
