# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import logging
import time
import traceback
from datetime import datetime
from typing import Set

import mozci.push
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

from bugbug import db, test_scheduling, utils
from bugbug.test_scheduling import ConfigGroup, Group

logger = logging.getLogger(__name__)


SHADOW_SCHEDULER_STATS_DB = "data/shadow_scheduler_stats.json"
db.register(
    SHADOW_SCHEDULER_STATS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/shadow_scheduler_stats.json.zst",
    3,
)


def analyze_shadow_schedulers(
    group_regressions: Set[Group],
    config_group_regressions: Set[ConfigGroup],
    push: mozci.push.Push,
) -> dict:
    schedulers = []

    for name, config_groups in push.generate_all_shadow_scheduler_config_groups():
        if isinstance(config_groups, mozci.errors.TaskNotFound):
            continue

        groups = set(group for config, group in config_groups)

        schedulers.append(
            {
                "name": name,
                "num_group_scheduled": len(groups),
                "num_group_regressions": len(group_regressions)
                if group_regressions is not None
                else None,
                "num_group_caught": len(group_regressions & groups)
                if group_regressions is not None
                else None,
                "num_config_group_scheduled": len(config_groups),
                "num_config_group_regressions": len(config_group_regressions)
                if config_group_regressions is not None
                else None,
                "num_config_group_caught": len(config_group_regressions & config_groups)
                if config_group_regressions is not None
                else None,
            }
        )

    return {
        "id": push.rev,
        "date": push.date,
        "schedulers": schedulers,
    }


def go(months: int) -> None:
    logger.info("Download previous shadow scheduler statistics...")
    db.download(SHADOW_SCHEDULER_STATS_DB)

    logger.info("Get previously gathered statistics...")
    scheduler_stats = {
        scheduler_stat["id"]: scheduler_stat
        for scheduler_stat in db.read(SHADOW_SCHEDULER_STATS_DB)
    }
    logger.info(f"Already gathered statistics for {len(scheduler_stats)} pushes...")

    to_date = datetime.utcnow() - relativedelta(days=3)
    from_date = to_date - relativedelta(months=months)
    pushes = mozci.push.make_push_objects(
        from_date=from_date.strftime("%Y-%m-%d"),
        to_date=to_date.strftime("%Y-%m-%d"),
        branch="autoland",
    )

    pushes_to_analyze = [push for push in pushes if push.rev not in scheduler_stats]

    logger.info(f"{len(pushes_to_analyze)} left to analyze")

    def compress_and_upload() -> None:
        db.write(
            SHADOW_SCHEDULER_STATS_DB,
            (
                scheduler_stats[push.rev]
                for push in pushes
                if push.rev in scheduler_stats
            ),
        )

        utils.zstd_compress(SHADOW_SCHEDULER_STATS_DB)
        db.upload(SHADOW_SCHEDULER_STATS_DB)

    assert db.download(test_scheduling.PUSH_DATA_GROUP_DB)
    group_regressions = {}
    for revisions, _, _, possible_regressions, likely_regressions in db.read(
        test_scheduling.PUSH_DATA_GROUP_DB
    ):
        group_regressions[revisions[0]] = set(likely_regressions)

    assert db.download(test_scheduling.PUSH_DATA_CONFIG_GROUP_DB)
    config_group_regressions = {}
    for (
        revisions,
        _,
        _,
        possible_regressions,
        likely_regressions,
    ) in db.read(test_scheduling.PUSH_DATA_CONFIG_GROUP_DB):
        config_group_regressions[revisions[0]] = set(
            tuple(r) for r in likely_regressions
        )

    start_time = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future_to_push = {
            executor.submit(
                analyze_shadow_schedulers,
                group_regressions[push.rev] if push.rev in group_regressions else None,
                config_group_regressions[push.rev]
                if push.rev in config_group_regressions
                else None,
                push,
            ): push
            for push in pushes_to_analyze
            if push.rev in group_regressions or push.rev in config_group_regressions
        }

        try:
            for future in tqdm(
                concurrent.futures.as_completed(future_to_push),
                total=len(future_to_push),
            ):
                push = future_to_push[future]

                try:
                    scheduler_stats[push.rev] = future.result()
                except Exception:
                    traceback.print_exc()

                # Upload every 10 minutes.
                if time.monotonic() - start_time >= 600:
                    compress_and_upload()
                    start_time = time.monotonic()
        except Exception:
            for f in future_to_push.keys():
                f.cancel()

            raise

    compress_and_upload()


def main() -> None:
    description = "Analyze results of shadow schedulers"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "months",
        type=int,
        help="How many months of pushes to analyze.",
    )
    args = parser.parse_args()

    go(args.months)


if __name__ == "__main__":
    main()
