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
from typing import Any, Iterable, Iterator

import matplotlib.pyplot as plt
import mozci.errors
import mozci.push
from dateutil.relativedelta import relativedelta
from mozci.task import get_configuration
from pandas import DataFrame
from tqdm import tqdm

from bugbug import db, test_scheduling, utils

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SHADOW_SCHEDULER_STATS_DB = "data/shadow_scheduler_stats.json"
db.register(
    SHADOW_SCHEDULER_STATS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/shadow_scheduler_stats.json.zst",
    4,
)

# Maximum number of pushes being analyzed at the same time. Shadow scheduler
# artifacts can be tens of MB each, so we can't have too many in flight.
MAX_IN_FLIGHT = 16

# Attributes where mozci memoizes large artifacts on Push objects.
PUSH_MEMOIZED_ATTRIBUTES = (
    "_decision_task",
    "_scheduled_task_labels",
    "_get_shadow_scheduler_tasks",
)


def analyze_shadow_schedulers(
    push: mozci.push.Push,
) -> dict[str, Any]:
    schedulers = []

    try:
        try:
            names = push.shadow_scheduler_names
        except mozci.errors.TaskNotFound:
            # The push has no decision task (e.g. DONTBUILD pushes).
            names = []

        # The task graph is not needed anymore.
        for attr in PUSH_MEMOIZED_ATTRIBUTES:
            push.__dict__.pop(attr, None)

        for name in names:
            try:
                tasks = push.get_shadow_scheduler_tasks(name)
            except mozci.errors.TaskNotFound, mozci.errors.ArtifactNotFound:
                # The shadow scheduler failed or didn't run on this push.
                continue
            finally:
                push.__dict__.pop("_get_shadow_scheduler_tasks", None)

            config_groups = {
                (get_configuration(task), group)
                for task in tasks
                for group in task["attributes"].get("test_manifests", [])
            }
            del tasks

            schedulers.append(
                {
                    "name": name,
                    "scheduled": list(config_groups),
                }
            )
    finally:
        for attr in PUSH_MEMOIZED_ATTRIBUTES:
            push.__dict__.pop(attr, None)

    return {
        "id": push.rev,
        "date": push.date,
        "schedulers": schedulers,
    }


def bounded_executor_map(
    executor: concurrent.futures.Executor,
    fn,
    iterables: Iterable[tuple],
    max_in_flight: int,
) -> Iterator[concurrent.futures.Future]:
    """Like executor.map, but yielding futures in completion order.

    Unlike executor.map, tasks are not all submitted at once and references to
    completed futures are not kept, so results can be garbage collected.
    """
    iterator = iter(iterables)
    pending: set[concurrent.futures.Future] = set()

    def submit_next() -> bool:
        try:
            args = next(iterator)
        except StopIteration:
            return False
        pending.add(executor.submit(fn, *args))
        return True

    try:
        for _ in range(max_in_flight):
            if not submit_next():
                break

        while pending:
            done, _ = concurrent.futures.wait(
                pending, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                pending.remove(future)
                submit_next()
                yield future
    finally:
        for future in pending:
            future.cancel()


def go(months: int) -> None:
    logger.info("Download previous shadow scheduler statistics...")
    db.download(SHADOW_SCHEDULER_STATS_DB)

    logger.info("Get previously gathered statistics...")
    known_scheduler_stats = {
        scheduler_stat["id"] for scheduler_stat in db.read(SHADOW_SCHEDULER_STATS_DB)
    }
    logger.info(
        "Already gathered statistics for %d pushes...", len(known_scheduler_stats)
    )

    to_date = datetime.utcnow() - relativedelta(days=3)
    from_date = to_date - relativedelta(months=months)
    pushes = mozci.push.make_push_objects(
        from_date=from_date.strftime("%Y-%m-%d"),
        to_date=to_date.strftime("%Y-%m-%d"),
        branch="autoland",
    )
    pushes = [push for push in pushes if push.rev not in known_scheduler_stats]
    del known_scheduler_stats

    # make_push_objects links each push to its parent and child, which means that
    # no push can be garbage collected as long as one of them is alive. We don't
    # need the links, so remove them.
    for push in pushes:
        push._parent = None
        push._child = None

    logger.info("%d left to analyze", len(pushes))

    def compress_and_upload() -> None:
        utils.zstd_compress(SHADOW_SCHEDULER_STATS_DB)
        db.upload(SHADOW_SCHEDULER_STATS_DB)

    results: list[dict[str, Any]] = []

    def flush() -> None:
        db.append(SHADOW_SCHEDULER_STATS_DB, results)
        results.clear()
        compress_and_upload()

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = bounded_executor_map(
            executor,
            analyze_shadow_schedulers,
            ((push,) for push in pushes),
            MAX_IN_FLIGHT,
        )

        start_time = time.monotonic()
        for future in tqdm(futures, total=len(pushes)):
            try:
                results.append(future.result())
            except Exception:
                # Transient failures, the push will be analyzed again on the next run.
                traceback.print_exc()

            # Upload every 10 minutes.
            if time.monotonic() - start_time >= 600:
                flush()
                start_time = time.monotonic()

    flush()


def plot_graph(df: DataFrame, title: str, file_path: str) -> None:
    ax = df.plot.bar(figsize=(20, 10))
    ax.set_title(title)
    ax.legend(loc="center left", bbox_to_anchor=(1, 0.5))

    plt.tight_layout()

    logger.info("Saving %s figure", file_path)
    plt.savefig(file_path)

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

        obj: dict[str, Any] = {
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

    if len(scheduled_data) == 0:
        logger.warning("No data to plot at the %s level", granularity)
        return

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
    granularity: str, scheduler1: str, scheduler2: str | None = None
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
            if scheduler2 is None or scheduler2 not in caught_by_scheduler:
                logger.info(
                    "%s didn't catch any of the %d regressions on %s",
                    scheduler1,
                    len(regressions),
                    rev,
                )
            elif len(caught_by_scheduler[scheduler2]) == 0:
                logger.info(
                    "%s and %s didn't catch any of the %d regressions on %s",
                    scheduler1,
                    scheduler2,
                    len(regressions),
                    rev,
                )
            else:
                logger.info(
                    "%s didn't catch any of the %d regressions on %s, while %s did",
                    scheduler1,
                    len(regressions),
                    rev,
                    scheduler2,
                )
            logger.info("Regressions: %s", regressions)
            logger.info(
                "Scheduled by %s: %s", scheduler1, scheduled_by_scheduler[scheduler1]
            )


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
