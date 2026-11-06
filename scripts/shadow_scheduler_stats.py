# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import logging
import math
import time
import traceback
from datetime import datetime
from typing import Any, Iterable, Iterator

import matplotlib.pyplot as plt
import mozci.errors
import mozci.push
from dateutil.relativedelta import relativedelta
from mozci.task import get_configuration
from pandas import DataFrame, DatetimeIndex, Series
from tqdm import tqdm

from bugbug import db, test_scheduling, utils

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SHADOW_SCHEDULER_STATS_DB = "data/shadow_scheduler_stats.json"
db.register(
    SHADOW_SCHEDULER_STATS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/shadow_scheduler_stats.json.zst",
    5,
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


def get_push_data_regressions(granularity: str, revs: set[str]) -> dict[str, set[Any]]:
    """Return the possible and likely regressions of the given pushes, indexed by revision."""
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
        if revisions[0] not in revs:
            continue

        regressions_by_rev[revisions[0]] = get_regressions(
            granularity, list(possible_regressions) + list(likely_regressions)
        )

    return regressions_by_rev


def analyze_shadow_schedulers(
    push: mozci.push.Push,
    group_candidates: set[str],
    config_group_candidates: set[tuple[str, str]],
) -> dict[str, Any]:
    """Analyze what the shadow schedulers would have scheduled on a push.

    Storing everything the shadow schedulers scheduled is not feasible (a single
    push can have hundreds of thousands of config/groups across all schedulers),
    so we only store how many groups and config/groups were scheduled and which
    of the given candidate regressions were scheduled.
    """
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

            groups = {group for config, group in config_groups}

            schedulers.append(
                {
                    "name": name,
                    "num_groups": len(groups),
                    "num_config_groups": len(config_groups),
                    "scheduled_regression_groups": sorted(groups & group_candidates),
                    "scheduled_regression_config_groups": sorted(
                        config_groups & config_group_candidates
                    ),
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

    revs = {push.rev for push in pushes}
    group_regressions = get_push_data_regressions("group", revs)
    config_group_regressions = get_push_data_regressions("config_group", revs)
    del revs

    # Pushes which are more recent than the most recent push with push data are
    # left for the next run, when push data for them will be available.
    last_with_push_data = max(
        (
            i
            for i, push in enumerate(pushes)
            if push.rev in group_regressions or push.rev in config_group_regressions
        ),
        default=-1,
    )
    logger.info(
        "Skipping %d pushes which don't have push data yet",
        len(pushes) - last_with_push_data - 1,
    )
    pushes = pushes[: last_with_push_data + 1]

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
            (
                (
                    push,
                    group_regressions.get(push.rev, set()),
                    config_group_regressions.get(push.rev, set()),
                )
                for push in pushes
            ),
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


def get_regressions(granularity, regressions):
    if granularity == "group":
        return set(translate_group(group) for group in regressions)
    else:
        return set((config, translate_group(group)) for config, group in regressions)


def get_num_scheduled(granularity, scheduler):
    return scheduler[f"num_{granularity}s"]


def get_scheduled_regressions(granularity, scheduler):
    if granularity == "group":
        return set(scheduler["scheduled_regression_groups"])
    else:
        return set(tuple(s) for s in scheduler["scheduled_regression_config_groups"])


def get_likely_regressions_by_rev(granularity: str) -> dict[str, set[Any]]:
    push_data_db = (
        test_scheduling.PUSH_DATA_GROUP_DB
        if granularity == "group"
        else test_scheduling.PUSH_DATA_CONFIG_GROUP_DB
    )
    assert db.download(push_data_db)

    return {
        revisions[0]: get_regressions(granularity, likely_regressions)
        for revisions, _, _, _, likely_regressions in db.read(push_data_db)
    }


# Schedulers with this prefix only change how performance tests are scheduled,
# so they schedule (almost) all test groups. They are reported separately, as
# they would otherwise dwarf the test selection schedulers.
PERF_SCHEDULER_PREFIX = "perf_"

HIGHLIGHT_COLOR = "#2a78d6"
CONTEXT_COLOR = "#c8c7c2"
TEXT_COLOR = "#52514e"


def is_perf_scheduler(name: str) -> bool:
    return name.startswith(PERF_SCHEDULER_PREFIX)


def get_stats_dataframes(granularity: str) -> tuple[DataFrame, DataFrame, Series]:
    """Return, for each push, the number of scheduled and caught regressions by scheduler.

    Values are NaN when a scheduler didn't run on a push.
    """
    regressions_by_rev = get_likely_regressions_by_rev(granularity)

    dates = []
    scheduled_data = []
    caught_data = []
    num_regressions = []

    for scheduler_stat in db.read(SHADOW_SCHEDULER_STATS_DB):
        if len(scheduler_stat["schedulers"]) == 0:
            continue

        if scheduler_stat["id"] not in regressions_by_rev:
            continue

        regressions = regressions_by_rev[scheduler_stat["id"]]

        dates.append(datetime.utcfromtimestamp(scheduler_stat["date"]))
        num_regressions.append(len(regressions))
        scheduled_data.append(
            {
                scheduler["name"]: get_num_scheduled(granularity, scheduler)
                for scheduler in scheduler_stat["schedulers"]
            }
        )
        caught_data.append(
            {
                scheduler["name"]: len(
                    regressions & get_scheduled_regressions(granularity, scheduler)
                )
                for scheduler in scheduler_stat["schedulers"]
            }
        )

    index = DatetimeIndex(dates, name="date")
    scheduled_df = DataFrame(scheduled_data, index=index).sort_index(axis=1)
    caught_df = DataFrame(caught_data, index=index).sort_index(axis=1)
    regressions = Series(num_regressions, index=index)

    return scheduled_df, caught_df, regressions


def compute_summary(
    scheduled_df: DataFrame, caught_df: DataFrame, regressions: Series
) -> DataFrame:
    """Summarize the performance of each scheduler on the given pushes."""
    ran = scheduled_df.notna()
    regressing = ran.mul(regressions > 0, axis=0)
    total_regressions = ran.mul(regressions, axis=0).sum()

    summary = DataFrame(
        {
            "pushes": ran.sum(),
            "avg scheduled": scheduled_df.mean(),
            "median scheduled": scheduled_df.median(),
            "pushes with regressions": regressing.sum(),
            "% regressing pushes caught": (
                (caught_df > 0)[regressing].sum() / regressing.sum() * 100
            ),
            "% regressions caught": caught_df.sum() / total_regressions * 100,
        }
    )
    summary.index.name = "scheduler"

    return summary.sort_values("avg scheduled")


def format_summary(summary: DataFrame) -> str:
    return summary.to_markdown(
        floatfmt=("", ".0f", ".1f", ".0f", ".0f", ".1f", ".1f"), intfmt=","
    )


def write_summary_table(
    granularity: str, summary: DataFrame, from_date: datetime, to_date: datetime
) -> None:
    perf = summary.index.map(is_perf_scheduler)

    content = f"""# Shadow schedulers at the {granularity} level

Pushes from {from_date:%Y-%m-%d} to {to_date:%Y-%m-%d}. Only likely regressions are considered.

- **avg scheduled**: average number of {granularity}s scheduled per push;
- **median scheduled**: median number of {granularity}s scheduled per push;
- **% regressing pushes caught**: percentage of pushes with regressions where at least one of them was scheduled;
- **% regressions caught**: percentage of all regressions which were scheduled.

## Test selection schedulers

{format_summary(summary[~perf])}
"""

    if perf.any():
        content += f"""
## Performance schedulers

These only change the scheduling of performance tests, so they run (almost) all test {granularity}s.

{format_summary(summary[perf])}
"""

    file_path = f"shadow_schedulers_{granularity}.md"
    logger.info("Saving %s:\n%s", file_path, content)
    with open(file_path, "w") as f:
        f.write(content)


def plot_tradeoff(granularity: str, summary: DataFrame, title: str) -> None:
    """Plot scheduled vs caught, so that the cost/benefit of each scheduler is visible."""
    max_x = summary["avg scheduled"].max()
    y_max = min(100, math.ceil(summary["% regressions caught"].max() * 1.2 / 10) * 10)

    # Schedulers with (almost) the same results would have overlapping points and
    # labels, so merge them.
    clusters: list[tuple[list[float], list[float], list[str]]] = []
    for name, row in summary.iterrows():
        x, y = row["avg scheduled"], row["% regressions caught"]
        for xs, ys, names in clusters:
            if abs(xs[0] - x) <= max_x * 0.01 and abs(ys[0] - y) <= y_max * 0.005:
                xs.append(x)
                ys.append(y)
                names.append(str(name))
                break
        else:
            clusters.append(([x], [y], [str(name)]))

    points = sorted(
        (sum(xs) / len(xs), sum(ys) / len(ys), names) for xs, ys, names in clusters
    )

    fig, ax = plt.subplots(figsize=(12, 8))

    # Many schedulers have similar results, so labels are placed in a column on
    # the right, spaced to avoid overlaps, and linked to their point.
    line_height = y_max * 0.025
    labels = []
    previous_top = float("-inf")
    for x, y, names in sorted(points, key=lambda point: point[1]):
        height = line_height * len(names)
        label_y = max(y, previous_top + height / 2)
        previous_top = label_y + height / 2
        labels.append((x, y, names, label_y))

    # Shift labels down if they overflow the top of the axis.
    overflow = max(0.0, previous_top - y_max)
    label_x = max_x * 1.1

    for x, y, names, label_y in labels:
        ax.scatter(x, y, s=64, color=HIGHLIGHT_COLOR, edgecolors="white", linewidths=2)
        ax.annotate(
            "\n".join(names),
            (x, y),
            xytext=(label_x, label_y - overflow),
            textcoords="data",
            va="center",
            fontsize=8,
            color=TEXT_COLOR,
            annotation_clip=False,
            arrowprops={"arrowstyle": "-", "color": CONTEXT_COLOR, "linewidth": 0.8},
        )

    ax.set_title(title, loc="left")
    ax.set_xlabel(f"Average number of scheduled {granularity}s per push")
    ax.set_ylabel("% of regressions caught")
    ax.set_xlim(0, max_x * 1.05)
    ax.set_ylim(0, y_max)
    ax.grid(color="#ebebe8")
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    plt.tight_layout()

    file_path = f"shadow_schedulers_{granularity}_tradeoff.svg"
    logger.info("Saving %s figure", file_path)
    plt.savefig(file_path)
    plt.close(fig)


def plot_weekly_trend(
    granularity: str, caught_df: DataFrame, regressions: Series
) -> None:
    """Plot, for each scheduler, the weekly percentage of regressions caught.

    Each scheduler gets its own panel, with the other schedulers in the background
    for context, as there are too many schedulers to tell them apart by color.
    """
    total_regressions = caught_df.notna().mul(regressions, axis=0)
    weekly = (
        caught_df.resample("W").sum(min_count=1)
        / total_regressions.resample("W").sum().replace(0, float("nan"))
        * 100
    )

    names = list(weekly.columns)
    ncols = 4
    nrows = math.ceil(len(names) / ncols)
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(4 * ncols, 2.5 * nrows),
        sharex=True,
        sharey=True,
        squeeze=False,
    )

    for ax, name in zip(axes.flat, names):
        for other in names:
            if other != name:
                ax.plot(weekly.index, weekly[other], color=CONTEXT_COLOR, linewidth=1)
        ax.plot(weekly.index, weekly[name], color=HIGHLIGHT_COLOR, linewidth=2)
        ax.set_title(name, fontsize=9, loc="left")
        ax.set_ylim(0, 100)
        ax.grid(axis="y", color="#ebebe8")
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        ax.tick_params(labelsize=8)

    for ax in axes.flat[len(names) :]:
        ax.set_visible(False)

    fig.suptitle(
        f"Weekly % of regressions caught at the {granularity} level", x=0.01, ha="left"
    )
    fig.autofmt_xdate()
    plt.tight_layout()

    file_path = f"shadow_schedulers_{granularity}_weekly.svg"
    logger.info("Saving %s figure", file_path)
    plt.savefig(file_path)
    plt.close(fig)


def report(granularity: str, weeks: int) -> None:
    scheduled_df, caught_df, regressions = get_stats_dataframes(granularity)

    if len(scheduled_df) == 0:
        logger.warning("No data to report at the %s level", granularity)
        return

    to_date = scheduled_df.index.max()
    from_date = to_date - relativedelta(weeks=weeks)
    recent = scheduled_df.index > from_date

    summary = compute_summary(
        scheduled_df[recent], caught_df[recent], regressions[recent]
    )
    summary = summary[summary["pushes"] > 0]
    write_summary_table(granularity, summary, from_date, to_date)

    test_selection = [name for name in summary.index if not is_perf_scheduler(name)]

    plot_tradeoff(
        granularity,
        summary.loc[test_selection],
        f"Scheduled {granularity}s vs regressions caught "
        f"({from_date:%Y-%m-%d} to {to_date:%Y-%m-%d})",
    )

    test_selection = [name for name in caught_df.columns if not is_perf_scheduler(name)]
    plot_weekly_trend(granularity, caught_df[test_selection], regressions)


def print_uncaught(
    granularity: str, scheduler1: str, scheduler2: str | None = None
) -> None:
    regressions_by_rev = get_likely_regressions_by_rev(granularity)

    for scheduler_stat in db.read(SHADOW_SCHEDULER_STATS_DB):
        if len(scheduler_stat["schedulers"]) == 0:
            continue

        rev = scheduler_stat["id"]

        if rev not in regressions_by_rev:
            continue

        regressions = regressions_by_rev[rev]

        if len(regressions) == 0:
            continue

        caught_by_scheduler = {
            scheduler["name"]: regressions
            & get_scheduled_regressions(granularity, scheduler)
            for scheduler in scheduler_stat["schedulers"]
        }

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
    parser.add_argument(
        "--weeks",
        type=int,
        default=4,
        help="How many weeks of pushes to summarize in the tables.",
    )
    args = parser.parse_args()

    go(args.months)
    report("group", args.weeks)
    report("config_group", args.weeks)
    if args.scheduler1:
        print_uncaught("group", args.scheduler1, args.scheduler2)


if __name__ == "__main__":
    main()
