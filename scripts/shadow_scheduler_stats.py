# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import logging
import traceback
from datetime import datetime
from typing import Iterator

import mozci.push
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

from bugbug import db, utils

logger = logging.getLogger(__name__)


SHADOW_SCHEDULER_STATS_DB = "data/shadow_scheduler_stats.json"
db.register(
    SHADOW_SCHEDULER_STATS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/shadow_scheduler_stats.json.zst",
    2,
)


def analyze_shadow_schedulers(push: mozci.push.Push) -> dict:
    schedulers = []

    group_regressions = push.get_likely_regressions("group")
    config_group_regressions = push.get_likely_regressions("config_group")

    for name, config_groups in push.generate_all_shadow_scheduler_config_groups():
        if isinstance(config_groups, mozci.errors.TaskNotFound):
            continue

        groups = set(group for config, group in config_groups)

        schedulers.append(
            {
                "name": name,
                "num_group_scheduled": len(groups),
                "num_group_regressions": len(group_regressions),
                "num_group_caught": len(group_regressions & groups),
                "num_config_group_scheduled": len(config_groups),
                "num_config_group_regressions": len(config_group_regressions),
                "num_config_group_caught": len(
                    config_group_regressions & config_groups
                ),
            }
        )

    return {
        "id": push.rev,
        "date": push.date,
        "schedulers": schedulers,
    }


def go(days: int) -> None:
    logger.info("Download previous shadow scheduler statistics...")
    db.download(SHADOW_SCHEDULER_STATS_DB)

    logger.info("Get previously gathered statistics...")
    prev_scheduler_stat_revs = set(
        scheduler_stat["id"] for scheduler_stat in db.read(SHADOW_SCHEDULER_STATS_DB)
    )
    logger.info(
        f"Already gathered statistics for {len(prev_scheduler_stat_revs)} pushes..."
    )

    to_date = datetime.utcnow() - relativedelta(days=3)
    from_date = to_date - relativedelta(days=days)
    pushes = mozci.push.make_push_objects(
        from_date=from_date.strftime("%Y-%m-%d"),
        to_date=to_date.strftime("%Y-%m-%d"),
        branch="autoland",
    )

    pushes = [push for push in pushes if push.rev not in prev_scheduler_stat_revs]

    logger.info(f"{len(pushes)} left to analyze")

    def compress_and_upload() -> None:
        utils.zstd_compress(SHADOW_SCHEDULER_STATS_DB)
        db.upload(SHADOW_SCHEDULER_STATS_DB)

    def results() -> Iterator[dict]:
        for i, push in enumerate(tqdm(pushes)):
            try:
                yield analyze_shadow_schedulers(push)
            except Exception:
                traceback.print_exc()

            # Upload every 42 pushes.
            if (i + 1) % 42 == 0:
                compress_and_upload()

    db.append(SHADOW_SCHEDULER_STATS_DB, results())
    compress_and_upload()


def main() -> None:
    description = "Analyze results of shadow schedulers"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "days",
        type=int,
        help="How many days of pushes to analyze.",
    )
    args = parser.parse_args()

    go(args.days)


if __name__ == "__main__":
    main()
