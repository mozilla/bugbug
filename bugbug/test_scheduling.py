# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import itertools
import logging
import os
import pickle
import shelve
import shutil
import struct
from functools import lru_cache
from typing import (
    Any,
    Callable,
    Deque,
    Iterator,
    List,
    NewType,
    Set,
    Tuple,
    Union,
    cast,
)

from tqdm import tqdm

from bugbug import db, repository
from bugbug.utils import ExpQueue, LMDBDict

logger = logging.getLogger(__name__)

Revision = NewType("Revision", str)
Task = NewType("Task", str)
Group = NewType("Group", str)
ConfigGroup = NewType("ConfigGroup", Tuple[str, Group])
Runnable = Union[Task, Group, ConfigGroup]
PushResult = Tuple[
    List[Revision], Tuple[Runnable, ...], Tuple[Runnable, ...], Tuple[Runnable, ...]
]

TEST_LABEL_SCHEDULING_DB = "data/test_label_scheduling_history.pickle"
PAST_FAILURES_LABEL_DB = "past_failures_label.lmdb.tar.zst"
FAILING_TOGETHER_LABEL_DB = "failing_together_label.lmdb.tar.zst"
db.register(
    TEST_LABEL_SCHEDULING_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_label_scheduling_history.latest/artifacts/public/test_label_scheduling_history.pickle.zst",
    13,
    [PAST_FAILURES_LABEL_DB, FAILING_TOGETHER_LABEL_DB],
)
PUSH_DATA_LABEL_DB = "data/push_data_label.json"
db.register(
    PUSH_DATA_LABEL_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_scheduling_history_push_data.latest/artifacts/public/push_data_label.json.zst",
    1,
)

TEST_GROUP_SCHEDULING_DB = "data/test_group_scheduling_history.pickle"
PAST_FAILURES_GROUP_DB = "past_failures_group.lmdb.tar.zst"
TOUCHED_TOGETHER_DB = "touched_together.lmdb.tar.zst"
db.register(
    TEST_GROUP_SCHEDULING_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_group_scheduling_history.latest/artifacts/public/test_group_scheduling_history.pickle.zst",
    1,
    [PAST_FAILURES_GROUP_DB, TOUCHED_TOGETHER_DB],
)
PUSH_DATA_GROUP_DB = "data/push_data_group.json"
db.register(
    PUSH_DATA_GROUP_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_scheduling_history_push_data.latest/artifacts/public/push_data_group.json.zst",
    1,
)

TEST_CONFIG_GROUP_SCHEDULING_DB = "data/test_config_group_scheduling_history.pickle"
PAST_FAILURES_CONFIG_GROUP_DB = "past_failures_config_group.lmdb.tar.zst"
FAILING_TOGETHER_CONFIG_GROUP_DB = "failing_together_config_group.lmdb.tar.zst"
db.register(
    TEST_CONFIG_GROUP_SCHEDULING_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_group_scheduling_history.latest/artifacts/public/test_config_group_scheduling_history.pickle.zst",
    20,
    [PAST_FAILURES_CONFIG_GROUP_DB, FAILING_TOGETHER_CONFIG_GROUP_DB],
)
PUSH_DATA_CONFIG_GROUP_DB = "data/push_data_config_group.json"
db.register(
    PUSH_DATA_CONFIG_GROUP_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_scheduling_history_push_data.latest/artifacts/public/push_data_config_group.json.zst",
    1,
)

HISTORICAL_TIMESPAN = 4500

JOBS_TO_CONSIDER = ("test-", "build-")
JOBS_TO_IGNORE = (
    "build-docker-image-",
    "-android-hw-",
    "-awsy-",
    "-raptor-",
    "-talos-",
    "backlog",
    # inclusive test suites -- these *only* run when certain files have changed
    "-test-verify-",
    "-test-coverage-",
    "jittest",
    "jsreftest",
    "android-hw-gfx",
)


def filter_runnables(
    runnables: Tuple[Runnable, ...], all_runnables: Set[Runnable], granularity: str
) -> Tuple[Any, ...]:
    if granularity == "label":
        tasks = cast(List[Task], runnables)
        return tuple(
            task
            for task in tasks
            if task in all_runnables
            and any(task.startswith(j) for j in JOBS_TO_CONSIDER)
            and not any(j in task for j in JOBS_TO_IGNORE)
        )
    else:
        return tuple(runnable for runnable in runnables if runnable in all_runnables)


# Handle "meaningless" labeling changes ("meaningless" as they shouldn't really affect test scheduling).
def rename_runnables(
    granularity: str, runnables: Tuple[Runnable, ...]
) -> Tuple[Runnable, ...]:
    if granularity == "label":
        tasks = cast(List[Task], runnables)
        return tuple(
            Task(task.replace("test-linux64-", "test-linux1804-64-")) for task in tasks
        )
    elif granularity == "group":
        groups = cast(List[Group], runnables)
        return tuple(Group(group.split(":")[0]) for group in groups)
    elif granularity == "config_group":
        config_groups = cast(List[ConfigGroup], runnables)
        return tuple(
            ConfigGroup(
                (
                    config.replace("test-linux64-", "test-linux1804-64-"),
                    Group(group.split(":")[0]),
                )
            )
            for config, group in config_groups
        )
    else:
        raise Exception(f"Unexpected {granularity} granularity")


def get_push_data(
    granularity: str,
) -> Tuple[Callable[[], Iterator[PushResult]], int, Tuple[Runnable, ...]]:
    if granularity == "label":
        push_data_db = PUSH_DATA_LABEL_DB
    elif granularity == "group":
        push_data_db = PUSH_DATA_GROUP_DB
    elif granularity == "config_group":
        push_data_db = PUSH_DATA_CONFIG_GROUP_DB

    assert db.download(push_data_db)

    # In the last 28 pushes, we definitely run all possible runnables.
    push_data_count = 0
    push_data_queue: Deque[PushResult] = collections.deque(maxlen=28)
    for elem in db.read(push_data_db):
        push_data_count += 1

        push_data_queue.append(elem)

    logger.info(f"push data nodes: {push_data_count}")

    push_data = [
        (
            revisions,
            rename_runnables(granularity, push_tasks),
            rename_runnables(granularity, possible_regressions),
            rename_runnables(granularity, likely_regressions),
        )
        for revisions, push_tasks, possible_regressions, likely_regressions in push_data_queue
    ]

    if granularity == "config_group":
        all_groups_set = set(
            sum(
                (
                    [Group(r[1]) for r in push_runnables]
                    for _, push_runnables, _, _ in push_data
                ),
                [],
            )
        )
        # Filter runnables we don't need.
        all_groups = filter_runnables(
            tuple(all_groups_set), cast(Set[Runnable], all_groups_set), "group"
        )
        all_groups_set = set(all_groups)
        logger.info(f"{len(all_groups_set)} manifests run in the last 28 pushes")

    all_runnables_set = set(
        sum((list(push_runnables) for _, push_runnables, _, _ in push_data), [])
    )
    # Filter runnables we don't need.
    all_runnables = filter_runnables(
        tuple(all_runnables_set), all_runnables_set, granularity
    )
    all_runnables_set = set(all_runnables)
    logger.info(f"{len(all_runnables_set)} runnables run in the last 28 pushes")

    def push_data_iter() -> Iterator[PushResult]:
        return (
            (
                revisions,
                filter_runnables(
                    rename_runnables(granularity, push_tasks),
                    all_runnables_set,
                    granularity,
                ),
                filter_runnables(
                    rename_runnables(granularity, possible_regressions),
                    all_runnables_set,
                    granularity,
                ),
                filter_runnables(
                    rename_runnables(granularity, likely_regressions),
                    all_runnables_set,
                    granularity,
                ),
            )
            for revisions, push_tasks, possible_regressions, likely_regressions in db.read(
                push_data_db
            )
        )

    if granularity == "config_group":
        manifest_combinations = sum(
            sum(1 for _ in itertools.combinations(sorted(group_tasks), 2))
            for manifest, group_tasks in itertools.groupby(
                sorted(all_runnables, key=lambda x: x[1]), key=lambda x: x[1]
            )
        )

        print(
            f"{manifest_combinations} possible combinations of manifests on configurations"
        )

    return push_data_iter, push_data_count, all_runnables


def get_test_scheduling_history(granularity):
    if granularity == "label":
        test_scheduling_db = TEST_LABEL_SCHEDULING_DB
    elif granularity == "group":
        test_scheduling_db = TEST_GROUP_SCHEDULING_DB
    elif granularity == "config_group":
        test_scheduling_db = TEST_CONFIG_GROUP_SCHEDULING_DB
    else:
        raise Exception(f"{granularity} granularity unsupported")

    for obj in db.read(test_scheduling_db):
        yield obj["revs"], obj["data"]


def get_past_failures(granularity):
    if granularity == "label":
        past_failures_db = os.path.join("data", PAST_FAILURES_LABEL_DB)
    elif granularity == "group":
        past_failures_db = os.path.join("data", PAST_FAILURES_GROUP_DB)
    elif granularity == "config_group":
        past_failures_db = os.path.join("data", PAST_FAILURES_CONFIG_GROUP_DB)
    else:
        raise Exception(f"{granularity} granularity unsupported")

    return shelve.Shelf(
        LMDBDict(past_failures_db[: -len(".tar.zst")]),
        protocol=pickle.DEFAULT_PROTOCOL,
        writeback=True,
    )


def get_failing_together_db_path(granularity: str) -> str:
    if granularity == "label":
        path = FAILING_TOGETHER_LABEL_DB
    elif granularity == "config_group":
        path = FAILING_TOGETHER_CONFIG_GROUP_DB
    else:
        raise Exception(f"{granularity} granularity unsupported")

    return os.path.join("data", path[: -len(".tar.zst")])


@lru_cache(maxsize=None)
def get_failing_together_db(granularity: str) -> LMDBDict:
    return LMDBDict(get_failing_together_db_path(granularity))


def failing_together_key(couple):
    if isinstance(couple[0], str):
        return f"{couple[0]}${couple[1]}".encode("utf-8")
    elif isinstance(couple[0], tuple):
        return f"{couple[0][0]}@{couple[0][1]}${couple[1][0]}@{couple[1][0]}".encode(
            "utf-8"
        )


def remove_failing_together_db(granularity: str) -> None:
    shutil.rmtree(
        get_failing_together_db_path(granularity), ignore_errors=True,
    )


def close_failing_together_db(granularity: str) -> None:
    get_failing_together_db(granularity).close()
    get_failing_together_db.cache_clear()


def generate_failing_together_probabilities(
    granularity: str,
    push_data: Iterator[PushResult],
    push_data_count: int,
    up_to: str = None,
) -> None:
    # TODO: we should consider the probabilities of `task1 failure -> task2 failure` and
    # `task2 failure -> task1 failure` separately, as they could be different.

    remove_failing_together_db(granularity)

    count_runs: collections.Counter = collections.Counter()
    count_single_failures: collections.Counter = collections.Counter()
    count_both_failures: collections.Counter = collections.Counter()

    def count_runs_and_failures(tasks):
        for task1, task2 in itertools.combinations(sorted(tasks), 2):
            count_runs[(task1, task2)] += 1

            if task1 in failures:
                if task2 in failures:
                    count_both_failures[(task1, task2)] += 1
                else:
                    count_single_failures[(task1, task2)] += 1
            elif task2 in failures:
                count_single_failures[(task1, task2)] += 1

    for revisions, tasks, likely_regressions, candidate_regressions in tqdm(
        push_data, total=push_data_count
    ):
        failures = set(likely_regressions + candidate_regressions)
        all_tasks = list(set(tasks) | failures)

        # At config/group granularity, only consider redundancy between the same manifest
        # on different configurations, and not between manifests too.
        if granularity == "config_group":
            groups = itertools.groupby(
                sorted(all_tasks, key=lambda x: x[1]), key=lambda x: x[1]
            )
            for manifest, group_tasks in groups:
                count_runs_and_failures(group_tasks)
        else:
            count_runs_and_failures(all_tasks)

        if up_to is not None and revisions[0] == up_to:
            break

    stats = {}

    skipped = 0

    for couple, run_count in count_runs.most_common():
        failure_count = count_both_failures[couple]
        single_failure_count = count_single_failures[couple]
        support = failure_count / run_count

        # At manifest-level, don't filter based on support.
        if granularity != "config_group" and support < 1 / 700:
            skipped += 1
            continue

        # At manifest-level, consider failures to be platform independent unless
        # proven otherwise.
        if failure_count != 0:
            confidence = failure_count / (single_failure_count + failure_count)
        elif single_failure_count == 0 and granularity == "config_group":
            confidence = 1.0
        else:
            confidence = 0.0

        stats[couple] = (support, confidence)

    logger.info(f"{skipped} couples skipped because their support was too low")

    logger.info("Redundancies with the highest support and confidence:")
    for couple, (support, confidence) in sorted(
        stats.items(), key=lambda k: (-k[1][1], -k[1][0])
    )[:7]:
        failure_count = count_both_failures[couple]
        run_count = count_runs[couple]
        logger.info(
            f"{couple[0]} - {couple[1]} redundancy confidence {confidence}, support {support} ({failure_count} over {run_count})."
        )

    logger.info("Redundancies with the highest confidence and lowest support:")
    for couple, (support, confidence) in sorted(
        stats.items(), key=lambda k: (-k[1][1], k[1][0])
    )[:7]:
        failure_count = count_both_failures[couple]
        run_count = count_runs[couple]
        logger.info(
            f"{couple[0]} - {couple[1]} redundancy confidence {confidence}, support {support} ({failure_count} over {run_count})."
        )

    failing_together = get_failing_together_db(granularity)
    count_redundancies: collections.Counter = collections.Counter()
    for couple, (support, confidence) in stats.items():
        if confidence == 1.0:
            count_redundancies["==100%"] += 1
        if confidence > 0.9:
            count_redundancies[">=90%"] += 1
        if confidence > 0.8:
            count_redundancies[">=80%"] += 1
        if confidence > 0.7:
            count_redundancies[">=70%"] += 1
        if confidence > 0.6:
            count_redundancies[">=60%"] += 1
        if confidence > 0.5:
            count_redundancies[">=50%"] += 1
        if confidence > 0.4:
            count_redundancies[">=40%"] += 1
        if confidence > 0.3:
            count_redundancies[">=30%"] += 1
        if confidence > 0.2:
            count_redundancies[">=20%"] += 1
        if confidence > 0.1:
            count_redundancies[">=10%"] += 1
        if confidence > 0.0:
            count_redundancies[">0%"] += 1

        if confidence == 0.0:
            count_redundancies["0%"] += 1
            continue

        failing_together[failing_together_key(couple)] = struct.pack(
            "ff", support, confidence
        )

    for percentage, count in count_redundancies.most_common():
        logger.info(f"{count} with {percentage} confidence")

    close_failing_together_db(granularity)


touched_together = None


def get_touched_together_db():
    global touched_together
    if touched_together is None:
        touched_together = LMDBDict(
            os.path.join("data", TOUCHED_TOGETHER_DB[: -len(".tar.zst")])
        )
    return touched_together


def close_touched_together_db():
    global touched_together
    touched_together.close()
    touched_together = None


def get_touched_together_key(f1, f2):
    # Always sort in lexographical order, so we are sure the output key is consistently
    # the same with the same two files as input, no matter their order.
    if f2 < f1:
        f1, f2 = f2, f1

    return f"{f1}${f2}".encode("utf-8")


def get_touched_together(f1, f2):
    touched_together = get_touched_together_db()

    key = get_touched_together_key(f1, f2)

    try:
        return struct.unpack("I", touched_together[key])[0]
    except KeyError:
        return 0


def set_touched_together(f1, f2):
    touched_together = get_touched_together_db()

    key = get_touched_together_key(f1, f2)

    if key not in touched_together:
        touched_together[key] = struct.pack("I", 1)
    else:
        touched_together[key] = struct.pack(
            "I", struct.unpack("I", touched_together[key])[0] + 1
        )


def update_touched_together():
    touched_together = get_touched_together_db()
    last_analyzed = (
        touched_together[b"last_analyzed"]
        if b"last_analyzed" in touched_together
        else None
    )

    # We can start once we get to the last revision we added in the previous run.
    can_start = True if last_analyzed is None else False

    seen = set()

    end_revision = yield

    for commit in repository.get_commits():
        seen.add(commit["node"])

        if can_start:
            touched_together[b"last_analyzed"] = commit["node"].encode("ascii")

            # As in the test scheduling history retriever script, for now skip commits which are too large.
            # Skip backed-out commits since they are usually relanded and we don't want to count them twice.
            if len(commit["files"]) <= 50 and not commit["backedoutby"]:
                # Number of times a source file was touched together with a directory.
                for f1 in commit["files"]:
                    for d2 in set(
                        os.path.dirname(f) for f in commit["files"] if f != f1
                    ):
                        set_touched_together(f1, d2)

                # Number of times a directory was touched together with another directory.
                for d1, d2 in itertools.combinations(
                    list(set(os.path.dirname(f) for f in commit["files"])), 2
                ):
                    set_touched_together(d1, d2)

        elif last_analyzed == commit["node"].encode("ascii"):
            can_start = True

        if commit["node"] == end_revision:
            # Some commits could be in slightly different order between mozilla-central and autoland.
            # It's a small detail that shouldn't affect the features, but we need to take it into account.
            while end_revision in seen:
                end_revision = yield

            if end_revision is None:
                break

    close_touched_together_db()


def _read_and_update_past_failures(
    past_failures, type_, runnable, items, push_num, is_regression
):
    values_total = []
    values_prev_700 = []
    values_prev_1400 = []
    values_prev_2800 = []

    key = f"{type_}${runnable}$"

    for item in items:
        full_key = key + item

        is_new = full_key not in past_failures

        if is_new:
            if not is_regression:
                continue

            cur = ExpQueue(round(push_num / 100), int(HISTORICAL_TIMESPAN / 100) + 1, 0)
        else:
            cur = past_failures[full_key]

        value = cur[round(push_num / 100)]

        values_total.append(value)
        values_prev_700.append(value - cur[round((push_num - 700) / 100)])
        values_prev_1400.append(value - cur[round((push_num - 1400) / 100)])
        values_prev_2800.append(value - cur[round((push_num - 2800) / 100)])

        if is_regression:
            cur[round(push_num / 100)] = value + 1
            if is_new:
                past_failures[full_key] = cur

    return (
        sum(values_total),
        sum(values_prev_700),
        sum(values_prev_1400),
        sum(values_prev_2800),
    )


def generate_data(
    past_failures, commit, push_num, runnables, possible_regressions, likely_regressions
):
    source_file_dirs = tuple(
        os.path.dirname(source_file) for source_file in commit["files"]
    )

    for runnable in runnables:
        if isinstance(runnable, tuple):
            runnable_dir = os.path.dirname(runnable[1])
        else:
            runnable_dir = os.path.dirname(runnable)

        touched_together_files = sum(
            get_touched_together(source_file, runnable_dir)
            for source_file in commit["files"]
        )
        touched_together_directories = sum(
            get_touched_together(source_file_dir, runnable_dir)
            for source_file_dir in source_file_dirs
        )

        is_possible_regression = runnable in possible_regressions
        is_likely_regression = runnable in likely_regressions

        is_regression = is_possible_regression or is_likely_regression

        (
            total_failures,
            past_700_pushes_failures,
            past_1400_pushes_failures,
            past_2800_pushes_failures,
        ) = _read_and_update_past_failures(
            past_failures, "all", runnable, ("all",), push_num, is_regression
        )

        (
            total_types_failures,
            past_700_pushes_types_failures,
            past_1400_pushes_types_failures,
            past_2800_pushes_types_failures,
        ) = _read_and_update_past_failures(
            past_failures, "type", runnable, commit["types"], push_num, is_regression,
        )

        (
            total_files_failures,
            past_700_pushes_files_failures,
            past_1400_pushes_files_failures,
            past_2800_pushes_files_failures,
        ) = _read_and_update_past_failures(
            past_failures, "file", runnable, commit["files"], push_num, is_regression,
        )

        (
            total_directories_failures,
            past_700_pushes_directories_failures,
            past_1400_pushes_directories_failures,
            past_2800_pushes_directories_failures,
        ) = _read_and_update_past_failures(
            past_failures,
            "directory",
            runnable,
            commit["directories"],
            push_num,
            is_regression,
        )

        (
            total_components_failures,
            past_700_pushes_components_failures,
            past_1400_pushes_components_failures,
            past_2800_pushes_components_failures,
        ) = _read_and_update_past_failures(
            past_failures,
            "component",
            runnable,
            commit["components"],
            push_num,
            is_regression,
        )

        yield {
            "name": runnable,
            "failures": total_failures,
            "failures_past_700_pushes": past_700_pushes_failures,
            "failures_past_1400_pushes": past_1400_pushes_failures,
            "failures_past_2800_pushes": past_2800_pushes_failures,
            "failures_in_types": total_types_failures,
            "failures_past_700_pushes_in_types": past_700_pushes_types_failures,
            "failures_past_1400_pushes_in_types": past_1400_pushes_types_failures,
            "failures_past_2800_pushes_in_types": past_2800_pushes_types_failures,
            "failures_in_files": total_files_failures,
            "failures_past_700_pushes_in_files": past_700_pushes_files_failures,
            "failures_past_1400_pushes_in_files": past_1400_pushes_files_failures,
            "failures_past_2800_pushes_in_files": past_2800_pushes_files_failures,
            "failures_in_directories": total_directories_failures,
            "failures_past_700_pushes_in_directories": past_700_pushes_directories_failures,
            "failures_past_1400_pushes_in_directories": past_1400_pushes_directories_failures,
            "failures_past_2800_pushes_in_directories": past_2800_pushes_directories_failures,
            "failures_in_components": total_components_failures,
            "failures_past_700_pushes_in_components": past_700_pushes_components_failures,
            "failures_past_1400_pushes_in_components": past_1400_pushes_components_failures,
            "failures_past_2800_pushes_in_components": past_2800_pushes_components_failures,
            "touched_together_files": touched_together_files,
            "touched_together_directories": touched_together_directories,
            "is_possible_regression": is_possible_regression,
            "is_likely_regression": is_likely_regression,
        }
