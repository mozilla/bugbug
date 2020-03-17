# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import itertools
import os
import pickle
import shelve

from bugbug import db, repository
from bugbug.utils import ExpQueue, LMDBDict

TEST_LABEL_SCHEDULING_DB = "data/test_label_scheduling_history.pickle"
PAST_FAILURES_LABEL_DB = "past_failures_label.lmdb.tar.zst"
db.register(
    TEST_LABEL_SCHEDULING_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_label_scheduling_history.latest/artifacts/public/test_label_scheduling_history.pickle.zst",
    7,
    [PAST_FAILURES_LABEL_DB],
)

TEST_GROUP_SCHEDULING_DB = "data/test_group_scheduling_history.pickle"
PAST_FAILURES_GROUP_DB = "past_failures_group.lmdb.tar.zst"
TOUCHED_TOGETHER_DB = "touched_together.lmdb.tar.zst"
db.register(
    TEST_GROUP_SCHEDULING_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_group_scheduling_history.latest/artifacts/public/test_group_scheduling_history.pickle.zst",
    11,
    [PAST_FAILURES_GROUP_DB, TOUCHED_TOGETHER_DB],
)

HISTORICAL_TIMESPAN = 56


def get_test_scheduling_history(granularity):
    if granularity == "label":
        test_scheduling_db = TEST_LABEL_SCHEDULING_DB
    elif granularity == "group":
        test_scheduling_db = TEST_GROUP_SCHEDULING_DB
    else:
        raise Exception(f"{granularity} granularity unsupported")

    return db.read(test_scheduling_db)


def get_past_failures(granularity):
    if granularity == "label":
        past_failures_db = os.path.join("data", PAST_FAILURES_LABEL_DB)
    elif granularity == "group":
        past_failures_db = os.path.join("data", PAST_FAILURES_GROUP_DB)
    else:
        raise Exception(f"{granularity} granularity unsupported")

    return shelve.Shelf(
        LMDBDict(past_failures_db[: -len(".tar.zst")]),
        protocol=pickle.DEFAULT_PROTOCOL,
        writeback=True,
    )


touched_together = None


def get_touched_together_db():
    global touched_together
    if touched_together is None:
        touched_together = shelve.Shelf(
            LMDBDict(os.path.join("data", TOUCHED_TOGETHER_DB[: -len(".tar.zst")])),
            protocol=pickle.DEFAULT_PROTOCOL,
            writeback=True,
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

    return f"{f1}${f2}"


def get_touched_together(f1, f2):
    touched_together = get_touched_together_db()

    key = get_touched_together_key(f1, f2)

    if key not in touched_together:
        return 0

    return touched_together[key]


def set_touched_together(f1, f2):
    touched_together = get_touched_together_db()

    key = get_touched_together_key(f1, f2)

    if key not in touched_together:
        touched_together[key] = 1
    else:
        touched_together[key] += 1


def update_touched_together():
    touched_together = get_touched_together_db()
    last_analyzed = (
        touched_together["last_analyzed"]
        if "last_analyzed" in touched_together
        else None
    )

    # We can start once we get to the last revision we added in the previous run.
    can_start = True if last_analyzed is None else False

    seen = set()

    end_revision = yield

    i = 0

    for commit in repository.get_commits():
        seen.add(commit["node"])

        if can_start:
            touched_together["last_analyzed"] = commit["node"]

            # As in the test scheduling history retriever script, for now skip commits which are too large.
            if len(commit["files"]) <= 50 and not commit["ever_backedout"]:
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

                i += 1
                if i % 5000:
                    touched_together.sync()
        elif last_analyzed == commit["node"]:
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
    values_prev_7 = []
    values_prev_14 = []
    values_prev_28 = []
    values_prev_56 = []

    key = f"{type_}${runnable}$"

    for item in items:
        full_key = key + item

        if full_key not in past_failures:
            cur = past_failures[full_key] = ExpQueue(
                push_num, HISTORICAL_TIMESPAN + 1, 0
            )
        else:
            cur = past_failures[full_key]

        value = cur[push_num]

        values_total.append(value)
        values_prev_7.append(value - cur[push_num - 7])
        values_prev_14.append(value - cur[push_num - 14])
        values_prev_28.append(value - cur[push_num - 28])
        values_prev_56.append(value - cur[push_num - 56])

        if is_regression:
            cur[push_num] = value + 1

    return (
        sum(values_total),
        sum(values_prev_7),
        sum(values_prev_14),
        sum(values_prev_28),
        sum(values_prev_56),
    )


def generate_data(
    past_failures, commit, push_num, runnables, possible_regressions, likely_regressions
):
    for runnable in runnables:
        touched_together_files = sum(
            get_touched_together(source_file, os.path.dirname(runnable))
            for source_file in commit["files"]
        )
        touched_together_directories = sum(
            get_touched_together(
                os.path.dirname(source_file), os.path.dirname(runnable)
            )
            for source_file in commit["files"]
        )

        is_regression = (
            runnable in possible_regressions or runnable in likely_regressions
        )

        (
            total_failures,
            past_7_pushes_failures,
            past_14_pushes_failures,
            past_28_pushes_failures,
            past_56_pushes_failures,
        ) = _read_and_update_past_failures(
            past_failures, "all", runnable, ["all"], push_num, is_regression
        )

        (
            total_types_failures,
            past_7_pushes_types_failures,
            past_14_pushes_types_failures,
            past_28_pushes_types_failures,
            past_56_pushes_types_failures,
        ) = _read_and_update_past_failures(
            past_failures, "type", runnable, commit["types"], push_num, is_regression,
        )

        (
            total_files_failures,
            past_7_pushes_files_failures,
            past_14_pushes_files_failures,
            past_28_pushes_files_failures,
            past_56_pushes_files_failures,
        ) = _read_and_update_past_failures(
            past_failures, "file", runnable, commit["files"], push_num, is_regression,
        )

        (
            total_directories_failures,
            past_7_pushes_directories_failures,
            past_14_pushes_directories_failures,
            past_28_pushes_directories_failures,
            past_56_pushes_directories_failures,
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
            past_7_pushes_components_failures,
            past_14_pushes_components_failures,
            past_28_pushes_components_failures,
            past_56_pushes_components_failures,
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
            "failures_past_7_pushes": past_7_pushes_failures,
            "failures_past_14_pushes": past_14_pushes_failures,
            "failures_past_28_pushes": past_28_pushes_failures,
            "failures_past_56_pushes": past_56_pushes_failures,
            "failures_in_types": total_types_failures,
            "failures_past_7_pushes_in_types": past_7_pushes_types_failures,
            "failures_past_14_pushes_in_types": past_14_pushes_types_failures,
            "failures_past_28_pushes_in_types": past_28_pushes_types_failures,
            "failures_past_56_pushes_in_types": past_56_pushes_types_failures,
            "failures_in_files": total_files_failures,
            "failures_past_7_pushes_in_files": past_7_pushes_files_failures,
            "failures_past_14_pushes_in_files": past_14_pushes_files_failures,
            "failures_past_28_pushes_in_files": past_28_pushes_files_failures,
            "failures_past_56_pushes_in_files": past_56_pushes_files_failures,
            "failures_in_directories": total_directories_failures,
            "failures_past_7_pushes_in_directories": past_7_pushes_directories_failures,
            "failures_past_14_pushes_in_directories": past_14_pushes_directories_failures,
            "failures_past_28_pushes_in_directories": past_28_pushes_directories_failures,
            "failures_past_56_pushes_in_directories": past_56_pushes_directories_failures,
            "failures_in_components": total_components_failures,
            "failures_past_7_pushes_in_components": past_7_pushes_components_failures,
            "failures_past_14_pushes_in_components": past_14_pushes_components_failures,
            "failures_past_28_pushes_in_components": past_28_pushes_components_failures,
            "failures_past_56_pushes_in_components": past_56_pushes_components_failures,
            "touched_together_files": touched_together_files,
            "touched_together_directories": touched_together_directories,
            "is_possible_regression": runnable in possible_regressions,
            "is_likely_regression": runnable in likely_regressions,
        }
