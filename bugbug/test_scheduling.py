# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import itertools
import os
import pickle
import shelve
import sys

from bugbug import db, repository
from bugbug.utils import ExpQueue, LMDBDict

PUSH_DATA_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_scheduling_history_push_data.latest/artifacts/public/push_data_{granularity}.json.zst"

TEST_LABEL_SCHEDULING_DB = "data/test_label_scheduling_history.pickle"
PAST_FAILURES_LABEL_DB = "past_failures_label.lmdb.tar.zst"
FAILING_TOGETHER_LABEL_DB = "failing_together_label.lmdb.tar.zst"
db.register(
    TEST_LABEL_SCHEDULING_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_label_scheduling_history.latest/artifacts/public/test_label_scheduling_history.pickle.zst",
    10,
    [PAST_FAILURES_LABEL_DB, FAILING_TOGETHER_LABEL_DB],
)

TEST_GROUP_SCHEDULING_DB = "data/test_group_scheduling_history.pickle"
PAST_FAILURES_GROUP_DB = "past_failures_group.lmdb.tar.zst"
TOUCHED_TOGETHER_DB = "touched_together.lmdb.tar.zst"
db.register(
    TEST_GROUP_SCHEDULING_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_group_scheduling_history.latest/artifacts/public/test_group_scheduling_history.pickle.zst",
    13,
    [PAST_FAILURES_GROUP_DB, TOUCHED_TOGETHER_DB],
)

HISTORICAL_TIMESPAN = 2800


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


failing_together = None


def get_failing_together_db():
    global failing_together
    if failing_together is None:
        failing_together = LMDBDict(
            os.path.join("data", FAILING_TOGETHER_LABEL_DB[: -len(".tar.zst")])
        )
    return failing_together


def close_failing_together_db():
    global failing_together
    failing_together.close()
    failing_together = None


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

    if key not in touched_together:
        return 0

    return int.from_bytes(touched_together[key], sys.byteorder)


def set_touched_together(f1, f2):
    touched_together = get_touched_together_db()

    key = get_touched_together_key(f1, f2)

    if key not in touched_together:
        touched_together[key] = (1).to_bytes(4, sys.byteorder)
    else:
        touched_together[key] = (
            int.from_bytes(touched_together[key], sys.byteorder) + 1
        ).to_bytes(4, sys.byteorder)


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
            past_700_pushes_failures,
            past_1400_pushes_failures,
            past_2800_pushes_failures,
        ) = _read_and_update_past_failures(
            past_failures, "all", runnable, ["all"], push_num, is_regression
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
            "is_possible_regression": runnable in possible_regressions,
            "is_likely_regression": runnable in likely_regressions,
        }
