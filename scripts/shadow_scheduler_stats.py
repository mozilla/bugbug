# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import contextlib
import fnmatch
import gzip
import json
import logging
import math
import time
import traceback
from datetime import datetime
from typing import Any, Callable, Iterable, Iterator

import matplotlib.pyplot as plt
import mozci.errors
import mozci.push
import requests
from dateutil.relativedelta import relativedelta
from mozci.task import get_configuration
from mozci.util.taskcluster import get_artifact_url
from pandas import DataFrame, DatetimeIndex, Series
from tqdm import tqdm

from bugbug import db, test_scheduling, utils

utils.setup_mozci()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


SHADOW_SCHEDULER_STATS_DB = "data/shadow_scheduler_stats.json"
db.register(
    SHADOW_SCHEDULER_STATS_DB,
    "https://s3-us-west-2.amazonaws.com/communitytc-bugbug/data/shadow_scheduler_stats.json.zst",
    7,
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


# Granularities at which tasks, rather than groups, are evaluated:
# - "non_group": test tasks which don't run test groups (e.g. gtest);
# - "build": builds.
TASK_GRANULARITIES = ("non_group", "build")


def get_task_granularity(task: dict) -> str | None:
    """Return the task granularity at which the task is evaluated, if any.

    Tasks are classified by the strategy optimizing them, so that e.g. performance
    tasks (which have their own strategies) are excluded.
    """
    if task["attributes"].get("test_manifests"):
        return None

    strategies = (task.get("optimization") or {}).keys()
    if "test" in strategies:
        return "non_group"
    elif "build" in strategies:
        return "build"

    return None


def get_push_data_db(granularity: str) -> str:
    if granularity == "label" or granularity in TASK_GRANULARITIES:
        return test_scheduling.PUSH_DATA_LABEL_DB
    elif granularity == "group":
        return test_scheduling.PUSH_DATA_GROUP_DB
    elif granularity == "config_group":
        return test_scheduling.PUSH_DATA_CONFIG_GROUP_DB

    raise ValueError(f"Unknown granularity {granularity}")


def get_push_data_regressions(granularity: str, revs: set[str]) -> dict[str, set[Any]]:
    """Return the possible and likely regressions of the given pushes, indexed by revision."""
    push_data_db = get_push_data_db(granularity)
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


def is_backstop(push: mozci.push.Push) -> bool | None:
    """Whether the push is a backstop push, or None if it's unknown.

    On backstop pushes, the taskgraph doesn't restrict the manifests of the test
    tasks to the ones selected by bugbug, so the chunks of the tasks the shadow
    schedulers select include all manifests, and they schedule many more groups.
    """
    try:
        parameters = push.decision_task.get_artifact("public/parameters.yml")
    except mozci.errors.TaskNotFound, mozci.errors.ArtifactNotFound:
        return None

    return bool(parameters.get("backstop"))


# Schedulers which we create from bugbug's reply on the push (the decision task's
# bugbug-push-schedules.json), rather than from shadow scheduler tasks: they run
# the groups selected by bugbug at the given confidence level on exactly the
# configs it selected for them, without what the taskgraph adds (e.g. the other
# groups of the tasks running them).
PLAIN_BUGBUG_SCHEDULERS = {
    "bugbug_plain_config_selection_very_low": "very_low",
    "bugbug_plain_config_selection_low": "low",
    "bugbug_plain_config_selection_medium": "medium",
    "bugbug_plain_config_selection_high": "high",
}

# The thresholds the taskgraph uses for the confidence levels when bugbug doesn't
# return the ones matching its current models. There is none for the "very_low"
# level, which only exists with the thresholds computed when training the models:
# its scheduler doesn't run on pushes whose reply doesn't have them.
DEFAULT_CONFIDENCE_THRESHOLDS = {"low": 0.7, "medium": 0.8, "high": 0.9}


def get_bugbug_push_schedules(push: mozci.push.Push) -> dict | None:
    """Return bugbug's reply on the push, as used by the taskgraph, if available."""
    try:
        return push.decision_task.get_artifact("public/bugbug-push-schedules.json")
    except mozci.errors.TaskNotFound, mozci.errors.ArtifactNotFound:
        return None


def without_chunk(label: str) -> str:
    """Return a task label without its chunk number, as the taskgraph matches it with bugbug's reply."""
    parts = label.split("-")
    return "-".join(parts[:-1] if parts[-1].isdigit() else parts)


def get_tasks_by_group(
    push: mozci.push.Push,
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Return the test tasks (labels without chunk number) running each group on the push.

    Also return the label of one of the chunks of each of them.
    """
    target_tasks = set(push.decision_task.get_artifact("public/target-tasks.json"))
    content = push.decision_task.get_artifact(
        "public/manifests-by-task.json.gz"
    ).content
    # The content might have been decompressed already, depending on the response's encoding.
    with contextlib.suppress(gzip.BadGzipFile):
        content = gzip.decompress(content)

    tasks_by_group: dict[str, set[str]] = {}
    chunk_labels: dict[str, str] = {}
    for label, manifests in json.loads(content).items():
        if label not in target_tasks or not label.startswith("test-"):
            continue

        task = without_chunk(label)
        chunk_labels.setdefault(task, label)
        for manifest in manifests:
            tasks_by_group.setdefault(translate_group(manifest), set()).add(task)

    return tasks_by_group, chunk_labels


def get_task_configurations(
    push: mozci.push.Push, chunk_labels: dict[str, str]
) -> dict[str, str]:
    """Return the configurations of some test tasks of the push, by label without chunk number.

    `chunk_labels` gives the label of one of the chunks of each task (see get_tasks_by_group).
    The definitions of the tasks come from the push's task graph, or from its full task graph
    for the tasks which didn't run on the push.
    """
    definitions: dict[str, dict] = {}
    task_graph = push.decision_task.get_artifact("public/task-graph.json")
    for task in task_graph.values():
        definitions.setdefault(without_chunk(task["label"]), task)
    del task_graph

    missing = {chunk_labels[task] for task in chunk_labels.keys() - definitions.keys()}
    if missing:
        r = requests.get(
            get_artifact_url(push.decision_task.id, "public/full-task-graph.json"),
            timeout=(30, 300),
        )
        r.raise_for_status()
        for label, task in test_scheduling.get_full_graph_tasks(
            r.content, missing
        ).items():
            definitions[without_chunk(label)] = task

    configurations = {}
    for task in chunk_labels:
        if task not in definitions:
            continue

        try:
            configurations[task] = get_configuration(
                definitions[task], push.branch, push.rev
            )
        except Exception:
            logger.warning("Failed to get the configuration of %s", task, exc_info=True)

    return configurations


def plain_bugbug_schedulers(
    bugbug_data: dict,
    tasks_by_group: dict[str, set[str]],
    get_configurations: Callable[[set[str]], dict[str, str]],
    group_candidates: set[str],
    config_group_candidates: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    """What the plain bugbug schedulers select on a push (see PLAIN_BUGBUG_SCHEDULERS).

    The groups bugbug selects run on the push's tasks running them (`tasks_by_group`, see
    get_tasks_by_group) whose labels match the ones bugbug gives for them, as the taskgraph
    matches them: recent replies give the labels (without chunk number) of the tasks of the
    selected configs, older ones gave the configs as patterns over the labels.

    The configurations of the tasks, to compare with the ones of the regressions, are only
    retrieved (with `get_configurations`) for the tasks running groups with regressions.
    """
    groups = bugbug_data.get("groups", {})
    config_groups = bugbug_data.get("config_groups", {})
    thresholds = bugbug_data.get("confidence_thresholds", {}).get("groups", {})

    selections = {}
    for name, level in PLAIN_BUGBUG_SCHEDULERS.items():
        threshold = thresholds.get(level, DEFAULT_CONFIDENCE_THRESHOLDS.get(level))
        if threshold is None:
            continue

        selected: dict[str, set[str]] = {}
        for group, confidence in groups.items():
            if confidence <= threshold:
                continue

            tasks = {
                task
                for task in tasks_by_group.get(group, ())
                if any(
                    fnmatch.fnmatch(task, pattern)
                    for pattern in config_groups.get(group, [])
                )
            }
            if tasks:
                selected[group] = tasks
        selections[name] = selected

    candidate_groups = {group for config, group in config_group_candidates}
    tasks_to_check = {
        task
        for selected in selections.values()
        for group, tasks in selected.items()
        if group in candidate_groups
        for task in tasks
    }
    configurations = get_configurations(tasks_to_check) if tasks_to_check else {}

    return [
        {
            "name": name,
            "num_groups": len(selected),
            # The number of tasks running each group is (almost always) the number of
            # configurations running it, as the shadow schedulers count them.
            "num_config_groups": sum(len(tasks) for tasks in selected.values()),
            "scheduled_regression_groups": sorted(group_candidates & selected.keys()),
            "scheduled_regression_config_groups": sorted(
                config_group_candidates
                & {
                    (configurations[task], group)
                    for group, tasks in selected.items()
                    for task in tasks
                    if task in configurations
                }
            ),
        }
        for name, selected in selections.items()
    ]


def analyze_shadow_schedulers(
    push: mozci.push.Push,
    group_candidates: set[str],
    config_group_candidates: set[tuple[str, str]],
    label_candidates: set[str],
) -> dict[str, Any]:
    """Analyze what the shadow schedulers would have scheduled on a push.

    Storing everything the shadow schedulers scheduled is not feasible (a single
    push can have hundreds of thousands of config/groups across all schedulers),
    so we only store how many groups, config/groups and tasks which don't run
    groups were scheduled and which of the given candidate regressions were scheduled.
    """
    schedulers = []
    # Candidate regressions at each task granularity, according to the tasks
    # scheduled by any of the schedulers.
    regression_labels: dict[str, set[str]] = {
        granularity: set() for granularity in TASK_GRANULARITIES
    }

    try:
        try:
            names = push.shadow_scheduler_names
        except mozci.errors.TaskNotFound:
            # The push has no decision task (e.g. DONTBUILD pushes).
            names = []

        backstop = is_backstop(push) if names else None
        bugbug_data = get_bugbug_push_schedules(push) if names else None
        if bugbug_data is not None:
            tasks_by_group, chunk_labels = get_tasks_by_group(push)
            schedulers += plain_bugbug_schedulers(
                bugbug_data,
                tasks_by_group,
                lambda tasks: get_task_configurations(
                    push, {task: chunk_labels[task] for task in tasks}
                ),
                group_candidates,
                config_group_candidates,
            )

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
                (get_configuration(task, push.branch, push.rev), group)
                for task in tasks
                for group in task["attributes"].get("test_manifests", [])
            }
            labels: dict[str, set[str]] = {
                granularity: set() for granularity in TASK_GRANULARITIES
            }
            for task in tasks:
                task_granularity = get_task_granularity(task)
                if task_granularity is not None:
                    labels[task_granularity].add(task["label"])
            del tasks

            groups = {group for config, group in config_groups}
            for granularity in TASK_GRANULARITIES:
                regression_labels[granularity] |= labels[granularity] & label_candidates

            schedulers.append(
                {
                    "name": name,
                    "num_groups": len(groups),
                    "num_config_groups": len(config_groups),
                    "scheduled_regression_groups": sorted(groups & group_candidates),
                    "scheduled_regression_config_groups": sorted(
                        config_groups & config_group_candidates
                    ),
                    **{
                        key: value
                        for granularity in TASK_GRANULARITIES
                        for key, value in (
                            (f"num_{granularity}s", len(labels[granularity])),
                            (
                                f"scheduled_regression_{granularity}s",
                                sorted(labels[granularity] & label_candidates),
                            ),
                        )
                    },
                }
            )
    finally:
        for attr in PUSH_MEMOIZED_ATTRIBUTES:
            push.__dict__.pop(attr, None)

    return {
        "id": push.rev,
        "date": push.date,
        "backstop": backstop,
        "schedulers": schedulers,
        **{
            f"{granularity}_regressions": sorted(regression_labels[granularity])
            for granularity in TASK_GRANULARITIES
        },
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
    label_regressions = get_push_data_regressions("label", revs)
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
                    label_regressions.get(push.rev, set()),
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
    if granularity == "label" or granularity in TASK_GRANULARITIES:
        return set(regressions)
    elif granularity == "group":
        return set(translate_group(group) for group in regressions)
    else:
        return set((config, translate_group(group)) for config, group in regressions)


def get_unit(granularity: str) -> str:
    """Return how the scheduled runnables of the given granularity are called in reports."""
    return {"non_group": "non-group test task", "build": "build"}.get(
        granularity, granularity
    )


def get_num_scheduled(granularity, scheduler):
    return scheduler[f"num_{granularity}s"]


def get_scheduled_regressions(granularity, scheduler):
    if granularity in TASK_GRANULARITIES:
        return set(scheduler[f"scheduled_regression_{granularity}s"])
    elif granularity == "group":
        return set(scheduler["scheduled_regression_groups"])
    else:
        return set(tuple(s) for s in scheduler["scheduled_regression_config_groups"])


def get_likely_regressions_by_rev(granularity: str) -> dict[str, set[Any]]:
    """Return the likely regressions of each push, indexed by revision.

    At task granularities, only the tasks of the granularity are considered (e.g.
    test tasks running groups are already measured at the group and config/group
    levels).
    """
    push_data_db = get_push_data_db(granularity)
    assert db.download(push_data_db)

    regressions_by_rev = {
        revisions[0]: get_regressions(granularity, likely_regressions)
        for revisions, _, _, _, likely_regressions in db.read(push_data_db)
    }

    if granularity in TASK_GRANULARITIES:
        labels_by_rev = {
            scheduler_stat["id"]: set(scheduler_stat[f"{granularity}_regressions"])
            for scheduler_stat in db.read(SHADOW_SCHEDULER_STATS_DB)
        }
        # Regressions which no scheduler scheduled on their push can be classified
        # using other pushes.
        labels = set().union(*labels_by_rev.values())
        regressions_by_rev = {
            rev: regressions & (labels_by_rev[rev] | labels)
            for rev, regressions in regressions_by_rev.items()
            if rev in labels_by_rev
        }

    return regressions_by_rev


# Schedulers with this prefix only change how performance tests are scheduled,
# so they schedule (almost) all test groups. They are reported separately, as
# they would otherwise dwarf the test selection schedulers.
PERF_SCHEDULER_PREFIX = "perf_"

HIGHLIGHT_COLOR = "#2a78d6"
CONTEXT_COLOR = "#c8c7c2"
TEXT_COLOR = "#52514e"


def is_perf_scheduler(name: str) -> bool:
    return name.startswith(PERF_SCHEDULER_PREFIX)


# Schedulers which select tasks rather than groups. They are not supposed to pick
# tasks which run groups, so they are only evaluated at task granularities.
TASK_SCHEDULERS = {
    "bugbug_reduced",
    "bugbug_reduced_high",
    "bugbug_tasks_high",
    "bugbug_tasks_medium",
}


def is_evaluated(granularity: str, name: str) -> bool:
    """Whether the scheduler is evaluated at the given granularity."""
    if granularity in TASK_GRANULARITIES:
        return name not in PLAIN_BUGBUG_SCHEDULERS

    return name not in TASK_SCHEDULERS


def get_stats_dataframes(
    granularity: str,
) -> tuple[DataFrame, DataFrame, Series, Series]:
    """Return, for each push, the number of scheduled and caught regressions by scheduler.

    Values are NaN when a scheduler didn't run on a push. The last series tells
    whether each push is a backstop push (None if it's unknown).
    """
    regressions_by_rev = get_likely_regressions_by_rev(granularity)

    dates = []
    scheduled_data = []
    caught_data = []
    num_regressions = []
    backstop = []

    for scheduler_stat in db.read(SHADOW_SCHEDULER_STATS_DB):
        if len(scheduler_stat["schedulers"]) == 0:
            continue

        if scheduler_stat["id"] not in regressions_by_rev:
            continue

        regressions = regressions_by_rev[scheduler_stat["id"]]
        schedulers = [
            scheduler
            for scheduler in scheduler_stat["schedulers"]
            if is_evaluated(granularity, scheduler["name"])
        ]

        dates.append(datetime.utcfromtimestamp(scheduler_stat["date"]))
        num_regressions.append(len(regressions))
        backstop.append(scheduler_stat.get("backstop"))
        scheduled_data.append(
            {
                scheduler["name"]: get_num_scheduled(granularity, scheduler)
                for scheduler in schedulers
            }
        )
        caught_data.append(
            {
                scheduler["name"]: len(
                    regressions & get_scheduled_regressions(granularity, scheduler)
                )
                for scheduler in schedulers
            }
        )

    index = DatetimeIndex(dates, name="date")
    scheduled_df = DataFrame(scheduled_data, index=index).sort_index(axis=1)
    caught_df = DataFrame(caught_data, index=index).sort_index(axis=1)
    regressions = Series(num_regressions, index=index)

    return (
        scheduled_df,
        caught_df,
        regressions,
        Series(backstop, index=index, dtype=object),
    )


def compute_summary(
    scheduled_df: DataFrame, caught_df: DataFrame, regressions: Series
) -> DataFrame:
    """Summarize the performance of each scheduler on the given pushes."""
    ran = scheduled_df.notna()
    regressing = ran.mul(regressions > 0, axis=0)
    total_regressions = ran.mul(regressions, axis=0).sum()
    # Schedulers without regressions on the pushes have no percentages caught.
    num_regressing = regressing.sum()

    summary = DataFrame(
        {
            "pushes": ran.sum(),
            "avg scheduled": scheduled_df.mean(),
            "median scheduled": scheduled_df.median(),
            "pushes with regressions": num_regressing,
            "% regressing pushes caught": (
                (caught_df > 0)[regressing].sum()
                / num_regressing.replace(0, float("nan"))
                * 100
            ),
            "% regressions caught": (
                caught_df.sum() / total_regressions.replace(0, float("nan")) * 100
            ),
        }
    )
    summary.index.name = "scheduler"

    return summary.sort_values("avg scheduled")


def format_summary(summary: DataFrame) -> str:
    return summary.to_markdown(
        floatfmt=("", ".0f", ".1f", ".0f", ".0f", ".1f", ".1f"), intfmt=","
    )


def write_summary_table(
    granularity: str,
    summary: DataFrame,
    summaries_by_push_type: dict[str, DataFrame],
    from_date: datetime,
    to_date: datetime,
) -> None:
    perf = summary.index.map(is_perf_scheduler)

    unit = get_unit(granularity)
    plain_note = (
        """The bugbug_plain_* schedulers are not shadow schedulers: they run the groups
selected by bugbug on exactly the configs it selected for them (from the
decision task's bugbug-push-schedules.json), without what the taskgraph adds
on top (in particular the other groups of the tasks running them).

"""
        if summary.index.isin(list(PLAIN_BUGBUG_SCHEDULERS)).any()
        else ""
    )
    content = f"""# Shadow schedulers at the {unit} level

Pushes from {from_date:%Y-%m-%d} to {to_date:%Y-%m-%d}. Only likely regressions are considered.

- **avg scheduled**: average number of {unit}s scheduled per push;
- **median scheduled**: median number of {unit}s scheduled per push;
- **% regressing pushes caught**: percentage of pushes with regressions where at least one of them was scheduled;
- **% regressions caught**: percentage of all regressions which were scheduled.

## Test selection schedulers

{plain_note}Results on non-backstop and backstop pushes are also reported separately below
(pushes whose type is unknown are only in the first table):

- on non-backstop pushes, only some {unit}s run, so regressions are only known
  where they ran (or were retriggered or backfilled): schedulers selecting {unit}s
  like the ones that ran are favored;
- backstop pushes run everything, so their regressions are known for all
  {unit}s and the schedulers can be compared fairly. However, their test tasks
  run all manifests rather than only the ones selected by bugbug, so the
  schedulers select (and catch, through the other manifests of the tasks they
  select) more {unit}s than they would on other pushes.

{format_summary(summary[~perf])}
"""

    for push_type, push_type_summary in summaries_by_push_type.items():
        if len(push_type_summary) == 0:
            continue

        push_type_summary = push_type_summary[
            ~push_type_summary.index.map(is_perf_scheduler)
        ]
        content += f"""
### {push_type.capitalize()} pushes

{format_summary(push_type_summary)}
"""

    if perf.any():
        content += f"""
## Performance schedulers

These only change the scheduling of performance tests, so they run (almost) all {unit}s.

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
    ax.set_xlabel(f"Average number of scheduled {get_unit(granularity)}s per push")
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
        f"Weekly % of regressions caught at the {get_unit(granularity)} level",
        x=0.01,
        ha="left",
    )
    fig.autofmt_xdate()
    plt.tight_layout()

    file_path = f"shadow_schedulers_{granularity}_weekly.svg"
    logger.info("Saving %s figure", file_path)
    plt.savefig(file_path)
    plt.close(fig)


def report(granularity: str, weeks: int) -> None:
    scheduled_df, caught_df, regressions, backstop = get_stats_dataframes(granularity)

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

    summaries_by_push_type = {}
    for push_type, is_push_type in (
        ("non-backstop", backstop.map(lambda b: b is False)),
        ("backstop", backstop.map(lambda b: b is True)),
    ):
        pushes = recent & is_push_type.to_numpy(dtype=bool)
        push_type_summary = compute_summary(
            scheduled_df[pushes], caught_df[pushes], regressions[pushes]
        )
        summaries_by_push_type[push_type] = push_type_summary[
            push_type_summary["pushes"] > 0
        ]

    write_summary_table(
        granularity, summary, summaries_by_push_type, from_date, to_date
    )

    test_selection = [name for name in summary.index if not is_perf_scheduler(name)]

    plot_tradeoff(
        granularity,
        summary.loc[test_selection],
        f"Scheduled {get_unit(granularity)}s vs regressions caught "
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
    for granularity in TASK_GRANULARITIES:
        report(granularity, args.weeks)
    if args.scheduler1:
        print_uncaught("group", args.scheduler1, args.scheduler2)


if __name__ == "__main__":
    main()
