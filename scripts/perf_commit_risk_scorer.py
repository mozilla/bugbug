# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Score every commit of the perf pushes snapshot with the perf regression predictor.

Writes ``perf_commit_risk.json``: one record per commit and model version with
the commit's risk score, or the reason the predictor skips it. Each run scores
the commits of the pushes snapshot that have no current record for the model,
including ones skipped under a rule the predictor has since dropped, and merges
them into the previous snapshot, so a run cut short by its time budget is
resumed by the next one and a new model version rescores the history.
Evaluations replaying scheduling strategies over history read this dataset
instead of calling the prediction endpoint, which scores one push at a time.
"""

import argparse
import collections
import logging
import time
from datetime import datetime, timedelta

from bugbug import db
from bugbug.perf import commit_risk, datasets
from bugbug.repository import clone as clone_repository
from bugbug.utils import parse_timestamp, utcnow, zstd_compress

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Commits reach mozilla-central, which the pipeline's clone follows, up to a
# day after landing on autoland. Younger pushes are left for the next run
# rather than reported missing from the clone.
DEFAULT_UNTIL_DAYS = 2
DEFAULT_CHUNK_SIZE = 50


def score(
    repo_dir: str,
    model_dir: str | None = None,
    model_version: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    time_budget_hours: float | None = None,
    refresh_repo: bool = False,
) -> collections.Counter:
    """Extend the commit risk archive with the commits the current model has not scored.

    Returns the counts of scored, ignored, missing and unscored commits.
    """
    if not db.exists(datasets.PERF_PUSHES_DB) and not db.download(
        datasets.PERF_PUSHES_DB
    ):
        raise RuntimeError(f"Failed to download {datasets.PERF_PUSHES_DB}")
    previous: list[dict] = []
    if datasets.load_previous_snapshot(datasets.PERF_COMMIT_RISK_DB):
        previous = list(datasets.get_perf_commit_risk())

    model = commit_risk.load_model(model_dir)
    model_version = model_version or commit_risk.get_model_version(model)
    logger.info("Model version %s: %s", model_version, model.get_extra_data())

    scored = {
        record["node"]
        for record in previous
        if commit_risk.is_current_record(record, model_version)
    }
    if until is None:
        until = utcnow() - timedelta(days=DEFAULT_UNTIL_DAYS)
    commits = commit_risk.select_unscored_commits(
        datasets.get_perf_pushes(), scored, since, until
    )
    logger.info(
        "%d commits pushed before %s have no score from this model, %d do",
        len(commits),
        until,
        len(scored),
    )
    if limit is not None:
        commits = commits[:limit]

    if refresh_repo:
        logger.info("Refreshing the Mercurial clone in %s", repo_dir)
        clone_repository(repo_dir)

    deadline = (
        time.monotonic() + time_budget_hours * 3600 if time_budget_hours else None
    )
    stats: collections.Counter = collections.Counter()
    new_records = list(
        commit_risk.score_commits(
            model, model_version, repo_dir, commits, chunk_size, deadline, stats
        )
    )
    superseded = {(record["node"], record["model_version"]) for record in new_records}
    records = [
        record
        for record in previous
        if (record["node"], record["model_version"]) not in superseded
    ] + new_records
    records.sort(
        key=lambda record: (
            record["push_time"],
            record["push_id"],
            record["node"],
            record["model_version"],
        )
    )
    db.write(datasets.PERF_COMMIT_RISK_DB, records)
    zstd_compress(datasets.PERF_COMMIT_RISK_DB)
    logger.info(
        "Wrote %d records: %d commits scored, %d ignored, %d missing from the "
        "clone, %d left for the next run",
        len(records),
        stats["scored"],
        stats["ignored"],
        stats["missing"],
        stats["unscored"],
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-dir",
        required=True,
        help="Local Mercurial clone to read commit messages and diffs from.",
    )
    parser.add_argument(
        "--refresh-repo",
        action="store_true",
        help="Clone or pull --repo-dir from mozilla-central first. This reverts local changes and strips unpushed commits, so only use it on a dedicated clone, as the pipeline does.",
    )
    parser.add_argument(
        "--model-dir",
        help="Hugging Face checkpoint directory. Default: download the published checkpoint.",
    )
    parser.add_argument(
        "--model-version",
        help="Version recorded with the scores. Default: the checkpoint's own, else the published artifact's name.",
    )
    parser.add_argument(
        "--since",
        type=parse_timestamp,
        metavar="DATE",
        help="Only commits of pushes from this date (ISO 8601, UTC). Default: the snapshot start.",
    )
    parser.add_argument(
        "--until",
        type=parse_timestamp,
        metavar="DATE",
        help=f"Only commits of pushes before this date. Default: {DEFAULT_UNTIL_DAYS} days ago, when they have reached mozilla-central.",
    )
    parser.add_argument(
        "--limit", type=int, help="Score at most this many commits, oldest first."
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help="Commits described per hg log call. Default: %(default)s.",
    )
    parser.add_argument(
        "--time-budget",
        type=float,
        metavar="HOURS",
        help="Stop scoring after this long and write what was scored; the next run continues.",
    )
    args = parser.parse_args()
    score(
        args.repo_dir,
        args.model_dir,
        args.model_version,
        args.since,
        args.until,
        args.limit,
        args.chunk_size,
        args.time_budget,
        args.refresh_repo,
    )


if __name__ == "__main__":
    main()
