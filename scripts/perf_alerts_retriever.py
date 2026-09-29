# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Retrieve Perfherder alerts and derive performance regression datasets.

Three commands, run in order by the Taskcluster pipeline: ``retrieve``
snapshots alerts, pushes, regression bugs and task costs; ``generate`` applies
the labeling policy to produce regressions, scheduling pairs and push status;
``export`` writes the commit dataset for the regression predictor from a local
Mercurial clone. Each command reads the previous one's output through
``bugbug.db``, downloading it from the Taskcluster index when absent locally.
"""

import argparse
import logging
import os
from datetime import date, datetime, timedelta, timezone

from dateutil.relativedelta import relativedelta

from bugbug import db, perf, redash
from bugbug.utils import parse_timestamp, utcnow, zstd_compress

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

COST_COVERAGE_DAYS = 30
# Alerts fire up to two weeks after their culprit push (Perfherder's detection
# window), so pushes start earlier than alerts; three weeks leaves a margin.
PUSHES_LEAD_DAYS = 21

# CLI flag defaults. The pipeline tasks in infra/data-pipeline.yml pass no
# flags, so these are the production values.
DEFAULT_MONTHS = 14
DEFAULT_WINDOW_DAYS = 3
DEFAULT_RERETRIEVE_DAYS = 30
DEFAULT_COSTS_WINDOW_DAYS = 2
DEFAULT_SETTLE_DAYS = 21
DEFAULT_NEGATIVE_RATIO = 5.0
DEFAULT_SEED = 0
DEFAULT_TEST_FRACTION = 0.1


def _download(path: str) -> None:
    """Fetch a dataset from the Taskcluster index, failing loudly if it is missing."""
    if not db.download(path):
        raise RuntimeError(f"Failed to download {path}")


class Retriever:
    """The three pipeline commands, scoped to a set of repositories."""

    def __init__(self, repositories: list[str]) -> None:
        self.repositories = repositories

    def retrieve(
        self,
        months: int,
        window_days: int,
        reretrieve_days: int,
        costs_window_days: int,
        skip_alerts: bool = False,
        skip_pushes: bool = False,
        skip_bugs: bool = False,
        skip_costs: bool = False,
    ) -> None:
        """Snapshot the raw sources for the last ``months``.

        Alerts are refetched whole, since sheriffs keep editing them, and
        merged into the previous snapshot so nothing is lost; bugs are small
        and refetched whole; pushes and costs are large and append-only, so
        only the recent ``reretrieve_days`` are refreshed. The ``skip_*`` flags
        let a failed step be rerun alone.
        """
        client = redash.RedashClient(redash.get_redash_api_key())
        since = date.today() - relativedelta(months=months)

        if not skip_alerts:
            self.retrieve_alerts(client, since)

        if not skip_pushes:
            self.retrieve_pushes(client, since, window_days, reretrieve_days)

        if not skip_bugs:
            bug_ids = {
                alert["bug_number"]
                for alert in perf.get_perf_alerts()
                if alert["bug_number"]
            }
            db.write(perf.PERF_ALERT_BUGS_DB, perf.fetch_bugs(bug_ids))
            zstd_compress(perf.PERF_ALERT_BUGS_DB)

        if not skip_costs:
            self.retrieve_costs(client, since, costs_window_days, reretrieve_days)

    def retrieve_alerts(self, client: redash.RedashClient, since: date) -> None:
        """Refresh every alert the source still has and keep the ones it no longer returns.

        Perfherder keeps alerts for roughly a year and loses the job behind a
        datapoint after 120 days, so the merged snapshot is the only archive of
        older alerts and of their task labels.
        """
        previous: list[dict] = []
        have_previous = False
        try:
            have_previous = db.download(perf.PERF_ALERTS_DB)
        except Exception:
            logger.warning("Could not download the previous alerts DB")
        if have_previous or db.exists(perf.PERF_ALERTS_DB):
            previous = list(perf.get_perf_alerts())

        fetched = list(perf.fetch_alerts(client, self.repositories, since))
        merged = perf.merge_alerts(previous, fetched)
        logger.info(
            "Alerts snapshot: %d fetched, %d kept from the previous snapshot, %d total",
            len(fetched),
            len(merged) - len(fetched),
            len(merged),
        )
        db.write(perf.PERF_ALERTS_DB, merged)
        zstd_compress(perf.PERF_ALERTS_DB)

    def retrieve_costs(
        self,
        client: redash.RedashClient,
        since: date,
        window_days: int,
        reretrieve_days: int,
    ) -> None:
        """Refresh the recent window of costs and keep older rows untouched."""
        today = utcnow().date()
        cutoff = since
        have_previous = False
        try:
            have_previous = db.download(perf.PERF_TASK_COSTS_DB)
        except Exception:
            logger.warning("Could not download the previous costs DB")
        have_previous = have_previous or db.exists(perf.PERF_TASK_COSTS_DB)
        if have_previous:
            newest = max(
                (row["submission_date"] for row in perf.get_perf_task_costs()),
                default=None,
            )
            if newest:
                cutoff = max(
                    since, date.fromisoformat(newest) - timedelta(days=reretrieve_days)
                )

        def rows():
            if have_previous:
                for row in perf.get_perf_task_costs():
                    if row["submission_date"] < cutoff.isoformat():
                        yield perf.normalize_cost_row(row)
            yield from perf.fetch_task_costs(
                client, self.repositories, cutoff, today, window_days
            )

        new_path = perf.PERF_TASK_COSTS_DB.replace(".json", "_new.json")
        db.DATABASES[new_path] = db.DATABASES[perf.PERF_TASK_COSTS_DB]
        db.write(new_path, rows())
        os.replace(new_path, perf.PERF_TASK_COSTS_DB)
        del db.DATABASES[new_path]
        zstd_compress(perf.PERF_TASK_COSTS_DB)

    def retrieve_pushes(
        self,
        client: redash.RedashClient,
        since: date,
        window_days: int,
        reretrieve_days: int,
    ) -> None:
        """Extend the pushes snapshot, refetching the recent window whose jobs still change.

        Pushes start ``PUSHES_LEAD_DAYS`` before the alerts so the culprit of
        the earliest alert is present, and stop a day ago so no push is stored
        while its jobs are still running.
        """
        pushes = {}
        have_previous = False
        try:
            have_previous = db.download(perf.PERF_PUSHES_DB)
        except Exception:
            logger.warning("Could not download the previous pushes DB")
        if have_previous or db.exists(perf.PERF_PUSHES_DB):
            pushes = {push["push_id"]: push for push in perf.get_perf_pushes()}

        now = utcnow()
        time_from = datetime(
            since.year, since.month, since.day, tzinfo=timezone.utc
        ) - timedelta(days=PUSHES_LEAD_DAYS)
        stored = [
            time
            for push in pushes.values()
            if (time := parse_timestamp(push["time"])) is not None
        ]
        if stored:
            time_from = max(time_from, max(stored) - timedelta(days=reretrieve_days))
        # Jobs on the newest pushes are still running.
        time_to = now - timedelta(days=1)

        for push in perf.fetch_pushes(
            client, self.repositories, time_from, time_to, window_days
        ):
            pushes[push["push_id"]] = push

        db.write(
            perf.PERF_PUSHES_DB,
            (pushes[push_id] for push_id in sorted(pushes)),
        )
        zstd_compress(perf.PERF_PUSHES_DB)

    def get_settle_cutoff(self, settle_days: int, until: datetime | None) -> datetime:
        """End of the dataset window; ``until`` can only tighten it."""
        settle_before = utcnow() - timedelta(days=settle_days)
        if until is not None:
            if until > settle_before:
                logger.warning(
                    "--until %s is inside the settle window, using %s",
                    until.date(),
                    settle_before.date(),
                )
            settle_before = min(until, settle_before)
        return settle_before

    def get_negatives_start(self, since: datetime | None) -> datetime | None:
        """Where negatives begin: the first alert summary, unless ``since`` is later."""
        labeled_from = self.get_alerts_window_start()
        if since is not None and (labeled_from is None or since > labeled_from):
            labeled_from = since
        return labeled_from

    def generate(
        self,
        settle_days: int,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> None:
        """Apply the labeling policy and write the three derived datasets.

        Regressions come first; scheduling pairs and push status are views of
        them. Finishes by logging how well the cost snapshot covers recent runs.
        """
        for path in (
            perf.PERF_ALERTS_DB,
            perf.PERF_PUSHES_DB,
            perf.PERF_ALERT_BUGS_DB,
        ):
            if not db.exists(path):
                _download(path)

        settle_before = self.get_settle_cutoff(settle_days, until)
        labeled_from = self.get_negatives_start(since)
        logger.info(
            "Dataset window: culprit pushes from %s, negatives from %s, both before %s",
            since or "the first fetched alert",
            labeled_from,
            settle_before,
        )
        push_index = perf.PushIndex(perf.get_perf_pushes())
        bugs = {bug["id"]: bug for bug in perf.get_perf_alert_bugs()}

        regressions = list(
            perf.generate_regressions(
                perf.get_perf_alerts(), push_index, bugs, settle_before, since
            )
        )
        db.write(perf.PERF_REGRESSIONS_DB, regressions)
        zstd_compress(perf.PERF_REGRESSIONS_DB)

        def pairs():
            for repository in self.repositories:
                yield from perf.generate_scheduling_pairs(
                    regressions, push_index, repository
                )

        db.write(perf.PERF_PAIRS_DB, pairs())
        zstd_compress(perf.PERF_PAIRS_DB)

        def push_status():
            for repository in self.repositories:
                yield from perf.generate_push_status(
                    push_index, regressions, repository, settle_before, labeled_from
                )

        db.write(perf.PERF_PUSH_STATUS_DB, push_status())
        zstd_compress(perf.PERF_PUSH_STATUS_DB)
        logger.info(
            "Wrote %d regressions, %d pairs, %d push status rows",
            len(regressions),
            db.size(perf.PERF_PAIRS_DB),
            db.size(perf.PERF_PUSH_STATUS_DB),
        )
        self.report_cost_coverage(push_index)

    def get_alerts_window_start(self) -> datetime | None:
        """Earliest summary creation in the snapshot: pushes before it were never labeled."""
        created = [
            time
            for alert in perf.get_perf_alerts()
            if (time := parse_timestamp(alert["summary_created"])) is not None
        ]
        return min(created) if created else None

    def report_cost_coverage(self, push_index: perf.PushIndex) -> None:
        """Log how many recent perf runs match a cost record, so gaps are noticed early."""
        if not db.exists(perf.PERF_TASK_COSTS_DB):
            try:
                _download(perf.PERF_TASK_COSTS_DB)
            except Exception:
                logger.warning("No perf task costs DB available, skipping coverage")
                return

        # Recent pushes only, so memory stays bounded as the history grows.
        recent_after = utcnow() - timedelta(days=COST_COVERAGE_DAYS)
        recent = [
            push
            for push in push_index.by_id.values()
            if (time := parse_timestamp(push["time"])) is not None
            and time >= recent_after
        ]
        costs = perf.load_task_costs(perf.collect_push_task_ids(recent))
        totals = {"runs": 0, "runs_with_pool": 0, "runs_with_cost": 0}
        for push in recent:
            summary = perf.summarize_push_cost(push, costs)
            for key in totals:
                totals[key] += summary[key]
        logger.info(
            "Cost coverage over the last %d days: %d perf runs, %d with a worker "
            "pool, %d with cloud cost",
            COST_COVERAGE_DAYS,
            totals["runs"],
            totals["runs_with_pool"],
            totals["runs_with_cost"],
        )

    def export(
        self,
        repo_dir: str,
        settle_days: int,
        negative_ratio: float,
        seed: int,
        test_fraction: float,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> None:
        """Write the commit dataset for the regression predictor.

        Culprit commits come from the regressions, clean commits from the push
        status rows, and the message and diff of each from ``repo_dir``.
        """
        for path in (perf.PERF_REGRESSIONS_DB, perf.PERF_PUSH_STATUS_DB):
            if not db.exists(path):
                _download(path)

        settle_before = self.get_settle_cutoff(settle_days, until)
        logger.info(
            "Dataset window: pushes from %s before %s",
            since or "the snapshot start",
            settle_before,
        )
        regressions = [
            regression
            for regression in perf.get_perf_regressions()
            if perf.is_within_window(regression["push_time"], since, settle_before)
        ]
        statuses = [
            status
            for status in perf.read_push_status()
            if perf.is_within_window(status["push_time"], since, settle_before)
        ]
        selected = perf.select_export_commits(
            regressions, statuses, negative_ratio, seed
        )
        perf.assign_train_test_splits(selected, test_fraction)

        db.write(
            perf.PERF_REGRESSION_COMMITS_DB,
            perf.attach_commit_patches(selected, repo_dir),
        )
        zstd_compress(perf.PERF_REGRESSION_COMMITS_DB)
        logger.info(
            "Wrote %d commits with patches",
            db.size(perf.PERF_REGRESSION_COMMITS_DB),
        )


def _add_window_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the dataset window flags shared by ``generate`` and ``export``."""
    parser.add_argument(
        "--settle-days",
        type=int,
        default=DEFAULT_SETTLE_DAYS,
        help="Ignore pushes younger than this many days; their alerts may still be pending. Default: %(default)s.",
    )
    parser.add_argument(
        "--since",
        type=parse_timestamp,
        metavar="DATE",
        help="Only pushes from this date (ISO 8601, UTC). Default: the alerts snapshot start.",
    )
    parser.add_argument(
        "--until",
        type=parse_timestamp,
        metavar="DATE",
        help="Only pushes before this date; never later than the settle cutoff.",
    )


def main() -> None:
    """Parse the command line and dispatch to the matching ``Retriever`` command."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        action="append",
        dest="repositories",
        help="Repository to consider (repeatable). Defaults to autoland and mozilla-central.",
    )
    subparsers = parser.add_subparsers(dest="op", required=True)

    retrieve = subparsers.add_parser(
        "retrieve",
        help="Snapshot alerts, pushes, regression bugs and task costs.",
    )
    retrieve.add_argument(
        "--months",
        type=int,
        default=DEFAULT_MONTHS,
        help="How many months of alert history to snapshot. Default: %(default)s.",
    )
    retrieve.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help="Days per pushes query; smaller windows keep each Redash result small. Default: %(default)s.",
    )
    retrieve.add_argument(
        "--reretrieve-days",
        type=int,
        default=DEFAULT_RERETRIEVE_DAYS,
        help="Refetch pushes and costs newer than this, since their jobs and cost rows keep arriving. Default: %(default)s.",
    )
    retrieve.add_argument(
        "--costs-window-days",
        type=int,
        default=DEFAULT_COSTS_WINDOW_DAYS,
        help="Days per task costs query against BigQuery. Default: %(default)s.",
    )
    retrieve.add_argument(
        "--skip-alerts", action="store_true", help="Keep the existing alerts snapshot."
    )
    retrieve.add_argument(
        "--skip-pushes", action="store_true", help="Keep the existing pushes snapshot."
    )
    retrieve.add_argument(
        "--skip-bugs",
        action="store_true",
        help="Keep the existing regression bugs snapshot.",
    )
    retrieve.add_argument(
        "--skip-costs",
        action="store_true",
        help="Keep the existing task costs snapshot.",
    )

    generate = subparsers.add_parser(
        "generate",
        help="Derive labeled regressions, scheduling pairs and push status.",
    )
    _add_window_arguments(generate)

    export = subparsers.add_parser(
        "export",
        help="Export commit messages and diffs for the regression predictor.",
    )
    export.add_argument(
        "--repo-dir",
        required=True,
        help="Local Mercurial clone to read commit messages and diffs from.",
    )
    _add_window_arguments(export)
    export.add_argument(
        "--negative-ratio",
        type=float,
        default=DEFAULT_NEGATIVE_RATIO,
        help="Clean commits to sample per culprit commit. Default: %(default)s.",
    )
    export.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Random seed for the negative sample, so reruns pick the same commits. Default: %(default)s.",
    )
    export.add_argument(
        "--test-fraction",
        type=float,
        default=DEFAULT_TEST_FRACTION,
        help="Share of commits, the newest by push date, held out as the test split. Default: %(default)s.",
    )

    args = parser.parse_args()
    retriever = Retriever(args.repositories or list(perf.REPOSITORIES))

    if args.op == "retrieve":
        retriever.retrieve(
            args.months,
            args.window_days,
            args.reretrieve_days,
            args.costs_window_days,
            args.skip_alerts,
            args.skip_pushes,
            args.skip_bugs,
            args.skip_costs,
        )
    elif args.op == "generate":
        retriever.generate(args.settle_days, args.since, args.until)
    elif args.op == "export":
        retriever.export(
            args.repo_dir,
            args.settle_days,
            args.negative_ratio,
            args.seed,
            args.test_fraction,
            args.since,
            args.until,
        )


if __name__ == "__main__":
    main()
