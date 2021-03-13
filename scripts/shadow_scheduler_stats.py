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
from typing import Any, Dict, Optional

import matplotlib.pyplot as plt
import mozci.push
from dateutil.relativedelta import relativedelta
from pandas import DataFrame
from tqdm import tqdm

from bugbug import db, test_scheduling, utils

logger = logging.getLogger(__name__)


SHADOW_SCHEDULER_STATS_DB = "data/shadow_scheduler_stats.json"
db.register(
    SHADOW_SCHEDULER_STATS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/shadow_scheduler_stats.json.zst",
    4,
)


def analyze_shadow_schedulers(
    push: mozci.push.Push,
) -> Dict[str, Any]:
    schedulers = []

    for name, config_groups in push.generate_all_shadow_scheduler_config_groups():
        if isinstance(config_groups, mozci.errors.TaskNotFound):
            continue

        schedulers.append(
            {
                "name": name,
                "scheduled": list(config_groups),
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
    known_scheduler_stats = {
        scheduler_stat["id"] for scheduler_stat in db.read(SHADOW_SCHEDULER_STATS_DB)
    }
    logger.info(
        f"Already gathered statistics for {len(known_scheduler_stats)} pushes..."
    )

    to_date = datetime.utcnow() - relativedelta(days=3)
    from_date = to_date - relativedelta(months=months)
    pushes = mozci.push.make_push_objects(
        from_date=from_date.strftime("%Y-%m-%d"),
        to_date=to_date.strftime("%Y-%m-%d"),
        branch="autoland",
    )

    pushes = [push for push in pushes if push.rev not in known_scheduler_stats]
    logger.info(f"{len(pushes)} left to analyze")

    def compress_and_upload() -> None:
        utils.zstd_compress(SHADOW_SCHEDULER_STATS_DB)
        db.upload(SHADOW_SCHEDULER_STATS_DB)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = tuple(
            executor.submit(
                analyze_shadow_schedulers,
                push,
            )
            for push in pushes
        )
        del pushes

        def results():
            start_time = time.monotonic()

            try:
                for future in tqdm(
                    concurrent.futures.as_completed(futures),
                    total=len(futures),
                ):
                    try:
                        yield future.result()
                    except Exception:
                        traceback.print_exc()

                    # Upload every 10 minutes.
                    if time.monotonic() - start_time >= 600:
                        compress_and_upload()
                        start_time = time.monotonic()
            except Exception:
                for f in futures:
                    f.cancel()

                raise

        db.append(SHADOW_SCHEDULER_STATS_DB, results())

    compress_and_upload()


def plot_graph(df: DataFrame, title: str, file_path: str) -> None:
    df.plot.bar()

    plt.tight_layout()

    logger.info("Saving %s figure", file_path)
    plt.savefig(file_path)

    plt.show()

    plt.close()


GROUP_TRANSLATIONS = {
    "testing/web-platform/tests": "",
    "testing/web-platform/mozilla/tests": "/_mozilla",
}


def translate_group(group):
    group = group.split(":")[0]

    for prefix, value in GROUP_TRANSLATIONS.items():
        if group.startswith(prefix):
            return group.replace(prefix, value)

    return group


def get_regressions(granularity, likely_regressions, possible_regressions):
    if granularity == "group":
        return set(translate_group(group) for group in likely_regressions)
    else:
        return set(
            (config, translate_group(group)) for config, group in likely_regressions
        )


def get_scheduled(granularity, scheduler):
    if granularity == "group":
        return set(group for config, group in scheduler["scheduled"])
    else:
        return set(tuple(s) for s in scheduler["scheduled"])


def plot_graphs(granularity: str) -> None:
    push_data_db = (
        test_scheduling.PUSH_DATA_GROUP_DB
        if granularity == "group"
        else test_scheduling.PUSH_DATA_CONFIG_GROUP_DB
    )
    assert db.download(push_data_db)

    regressions_by_rev = {}
    for revisions, _, _, possible_regressions, likely_regressions in db.read(
        push_data_db
    ):
        regressions_by_rev[revisions[0]] = get_regressions(
            granularity, likely_regressions, possible_regressions
        )

    scheduled_data = []
    caught_data = []

    for scheduler_stat in db.read(SHADOW_SCHEDULER_STATS_DB):
        if len(scheduler_stat["schedulers"]) == 0:
            continue

        if scheduler_stat["id"] not in regressions_by_rev:
            continue

        obj: Dict[str, Any] = {
            "date": datetime.utcfromtimestamp(scheduler_stat["date"]),
        }

        for scheduler in scheduler_stat["schedulers"]:
            obj[scheduler["name"]] = len(get_scheduled(granularity, scheduler))

        scheduled_data.append(obj)

        regressions = regressions_by_rev[scheduler_stat["id"]]

        obj = {
            "date": datetime.utcfromtimestamp(scheduler_stat["date"]),
            "regressions": len(regressions),
        }

        for scheduler in scheduler_stat["schedulers"]:
            scheduled = get_scheduled(granularity, scheduler)

            obj[scheduler["name"]] = len(regressions & scheduled)

        caught_data.append(obj)

    scheduled_df = DataFrame(scheduled_data)
    scheduled_df.index = scheduled_df["date"]
    del scheduled_df["date"]

    caught_df = DataFrame(caught_data)
    caught_df.index = caught_df["date"]
    del caught_df["date"]

    df = scheduled_df.resample("W").mean()

    plot_graph(
        df,
        f"Average number of scheduled {granularity}s",
        f"average_{granularity}_scheduled.svg",
    )

    df = (
        caught_df[caught_df.regressions > 0]
        .drop(columns=["regressions"])
        .clip(0, 1)
        .resample("W")
        .mean()
    )

    plot_graph(
        df,
        "Percentage of regressing pushes where we caught at least one regression",
        f"percentage_{granularity}_caught_at_least_one.svg",
    )

    plot_graph(
        caught_df.drop(columns=["regressions"])
        .div(caught_df.regressions, axis=0)
        .resample("W")
        .mean(),
        "Percentage of regressions we caught",
        f"percentage_{granularity}_caught.svg",
    )


def print_uncaught(
    granularity: str, scheduler1: str, scheduler2: Optional[str] = None
) -> None:
    push_data_db = (
        test_scheduling.PUSH_DATA_GROUP_DB
        if granularity == "group"
        else test_scheduling.PUSH_DATA_CONFIG_GROUP_DB
    )
    assert db.download(push_data_db)

    regressions_by_rev = {}
    for revisions, _, _, possible_regressions, likely_regressions in db.read(
        push_data_db
    ):
        regressions_by_rev[revisions[0]] = get_regressions(
            granularity, likely_regressions, possible_regressions
        )

    for scheduler_stat in db.read(SHADOW_SCHEDULER_STATS_DB):
        if len(scheduler_stat["schedulers"]) == 0:
            continue

        rev = scheduler_stat["id"]

        if rev not in regressions_by_rev:
            continue

        regressions = regressions_by_rev[rev]

        if len(regressions) == 0:
            continue

        scheduled_by_scheduler = {}
        caught_by_scheduler = {}

        for scheduler in scheduler_stat["schedulers"]:
            scheduled = get_scheduled(granularity, scheduler)

            scheduled_by_scheduler[scheduler["name"]] = scheduled
            caught_by_scheduler[scheduler["name"]] = regressions & scheduled

        if scheduler1 not in caught_by_scheduler:
            continue

        if len(caught_by_scheduler[scheduler1]) == 0:
            if scheduler2 is not None and scheduler2 not in caught_by_scheduler:
                print(
                    f"{scheduler1} didn't catch any of the {len(regressions)} regressions on {rev}"
                )
            elif scheduler2 is not None and len(caught_by_scheduler[scheduler2]) == 0:
                print(
                    f"{scheduler1} and {scheduler2} didn't catch any of the {len(regressions)} regressions on {rev}"
                )
            else:
                print(
                    f"{scheduler1} didn't catch any of the {len(regressions)} regressions on {rev}, while {scheduler2} did"
                )
            print(f"Regressions: {regressions}")
            print(f"Scheduled by {scheduler1}: {scheduled_by_scheduler[scheduler1]}")


def main() -> None:
    description = "Analyze results of shadow schedulers"
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "months",
        type=int,
        help="How many months of pushes to analyze.",
    )
    parser.add_argument(
        "--scheduler1", type=str, help="Scheduler to analyze for uncaught regressions"
    )
    parser.add_argument(
        "--scheduler2",
        type=str,
        help="Scheduler to compare to for uncaught regressions",
    )
    args = parser.parse_args()

    go(args.months)
    plot_graphs("group")
    plot_graphs("config_group")
    if args.scheduler1:
        print_uncaught("group", args.scheduler1, args.scheduler2)


if __name__ == "__main__":
    main()
