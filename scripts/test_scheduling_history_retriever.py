# -*- coding: utf-8 -*-

import argparse
import json
import os
import pickle
import shelve
import subprocess
import tarfile
from datetime import datetime
from logging import INFO, basicConfig, getLogger

import dateutil.parser
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

from bugbug import commit_features, db, repository, test_scheduling
from bugbug.utils import ExpQueue, download_check_etag, zstd_compress, zstd_decompress

basicConfig(level=INFO)
logger = getLogger(__name__)

JOBS_TO_CONSIDER = ("test-", "build-")
JOBS_TO_IGNORE = ("build-docker-image-",)

ADR_CACHE_DB = "data/adr_cache.tar"
db.register(
    ADR_CACHE_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_scheduling_history_push_data.latest/artifacts/public/adr_cache.tar.zst",
    2,
    support_files=[],
)
PUSH_DATA_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_test_scheduling_history_push_data.latest/artifacts/public/push_data.json.zst"

TRAINING_MONTHS = 6


def filter_tasks(tasks, all_tasks):
    return tuple(
        task
        for task in tasks
        if task in all_tasks
        and any(task.startswith(j) for j in JOBS_TO_CONSIDER)
        and not any(task.startswith(j) for j in JOBS_TO_IGNORE)
    )


class Retriever(object):
    def __init__(self):
        os.makedirs("data", exist_ok=True)

    def retrieve_push_data(self):
        # Download previous cache.
        cache_path = os.path.splitext(ADR_CACHE_DB)[0]
        if not db.is_old_version(ADR_CACHE_DB):
            db.download(ADR_CACHE_DB)
            if os.path.exists(ADR_CACHE_DB):
                with tarfile.open(ADR_CACHE_DB, "r") as tar:
                    tar.extractall()
                assert os.path.exists(cache_path), "Decompressed adr cache exists"

        # Setup adr cache configuration.
        os.makedirs(os.path.expanduser("~/.config/adr"), exist_ok=True)
        with open(os.path.expanduser("~/.config/adr/config.toml"), "w") as f:
            f.write(
                f"""[adr.cache.stores]
file = {{ driver = "file", path = "{os.path.abspath(cache_path)}" }}
"""
            )

        # We'll use the past TRAINING_MONTHS months only for training the model,
        # but we use 3 months more than that to calculate the failure statistics.
        subprocess.run(
            [
                "run-adr",
                "ahal/ci-recipes",
                "recipe",
                "-o",
                os.path.abspath("push_data.json"),
                "-f",
                "json",
                "push_data",
                "--",
                "--from",
                f"today-{TRAINING_MONTHS + 3}month",
                "--to",
                "today-3day",
                "--branch",
                "autoland",
            ],
            check=True,
            stdout=subprocess.DEVNULL,  # Redirect to /dev/null, as the logs are too big otherwise.
        )

        with tarfile.open(ADR_CACHE_DB, "w") as tar:
            tar.add(cache_path)
        zstd_compress(ADR_CACHE_DB)

        zstd_compress("push_data.json")

    def generate_test_scheduling_history(self):
        if not os.path.exists("push_data.json"):
            download_check_etag(PUSH_DATA_URL, "push_data.json.zst")
            zstd_decompress("push_data.json")
            assert os.path.exists(
                "push_data.json"
            ), "Decompressed push data file exists"

        # Get the commits DB.
        if db.is_old_version(repository.COMMITS_DB) or not db.exists(
            repository.COMMITS_DB
        ):
            db.download(repository.COMMITS_DB, force=True)

        HISTORY_DATE_START = datetime.now() - relativedelta(months=TRAINING_MONTHS)

        HISTORICAL_TIMESPAN = 56

        if not db.is_old_version(test_scheduling.TEST_SCHEDULING_DB):
            db.download(test_scheduling.TEST_SCHEDULING_DB, support_files_too=True)

            for test_data in test_scheduling.get_test_scheduling_history():
                pass

            last_node = test_data["revs"][0]
        else:
            last_node = None

        past_failures = shelve.open(
            "data/past_failures.shelve",
            protocol=pickle.HIGHEST_PROTOCOL,
            writeback=True,
        )

        push_num = past_failures["push_num"] if "push_num" in past_failures else 0

        def get_and_update_past_failures(type_, task, items, push_num, is_regression):
            values_total = []
            values_prev_7 = []
            values_prev_14 = []
            values_prev_28 = []
            values_prev_56 = []

            key = f"{type_}${task}$"

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

        def generate_data():
            nonlocal push_num
            saved_nodes = set()
            skipped_no_commits = 0
            skipped_too_big_commits = 0
            skipped_no_tasks = 0

            # We can start once we get to the last revision we added in the previous run.
            can_start = True if last_node is None else False

            commit_map = {}
            for commit_data in tqdm(repository.get_commits()):
                if not can_start:
                    if last_node == commit_data["node"]:
                        can_start = True

                    continue

                commit_map[commit_data["node"]] = commit_data

            with open("push_data.json", "r") as f:
                push_data = json.load(f)[1:]

            logger.info(f"push data nodes: {len(push_data)}")

            # In the last 28 pushes, we definitely run all possible tasks.
            all_tasks_set = set(
                sum((push_tasks for _, push_tasks, _, _ in push_data[-28:]), [])
            )
            logger.info(f"{len(all_tasks_set)} tasks run in the last 28 pushes")
            all_tasks = list(all_tasks_set)

            # We can start once we get to the last revision we added in the previous run.
            can_start = True if last_node is None else False

            for i in tqdm(range(len(push_data))):
                (
                    revisions,
                    push_tasks,
                    possible_regressions,
                    likely_regressions,
                ) = push_data.pop(0)

                if not can_start:
                    if last_node == revisions[0]:
                        can_start = True

                    continue

                push_num += 1

                # XXX: Some commits are skipped in the repository mining, e.g. merges and backouts. Maybe we should not skip them.
                commits = tuple(
                    commit_map.pop(revision)
                    for revision in revisions
                    if revision in commit_map
                )
                if len(commits) == 0:
                    skipped_no_commits += 1
                    continue

                merged_commits = commit_features.merge_commits(commits)

                # XXX: For now, skip commits which are too large.
                # In the future we can either:
                #  - Improve shelve perf and go back to consider all files;
                #  - Consider only files which appear with a given frequency, like the "files" feature in commit_features;
                #  - Keep a limit of number of files.
                if len(merged_commits["files"]) > 50:
                    skipped_too_big_commits += 1
                    continue

                tasks = filter_tasks(push_tasks, all_tasks_set)

                if len(tasks) == 0:
                    skipped_no_tasks += 1
                    continue

                # Sync DB every 500 pushes, so we cleanup the shelve cache (we'd run OOM otherwise!).
                if i % 500 == 0:
                    past_failures.sync()

                pushdate = dateutil.parser.parse(merged_commits["pushdate"])

                for task in all_tasks:
                    is_regression = (
                        task in possible_regressions or task in likely_regressions
                    )

                    (
                        total_failures,
                        past_7_pushes_failures,
                        past_14_pushes_failures,
                        past_28_pushes_failures,
                        past_56_pushes_failures,
                    ) = get_and_update_past_failures(
                        "all", task, ["all"], push_num, is_regression
                    )

                    (
                        total_types_failures,
                        past_7_pushes_types_failures,
                        past_14_pushes_types_failures,
                        past_28_pushes_types_failures,
                        past_56_pushes_types_failures,
                    ) = get_and_update_past_failures(
                        "type", task, merged_commits["types"], push_num, is_regression
                    )

                    (
                        total_files_failures,
                        past_7_pushes_files_failures,
                        past_14_pushes_files_failures,
                        past_28_pushes_files_failures,
                        past_56_pushes_files_failures,
                    ) = get_and_update_past_failures(
                        "file", task, merged_commits["files"], push_num, is_regression
                    )

                    (
                        total_directories_failures,
                        past_7_pushes_directories_failures,
                        past_14_pushes_directories_failures,
                        past_28_pushes_directories_failures,
                        past_56_pushes_directories_failures,
                    ) = get_and_update_past_failures(
                        "directory",
                        task,
                        merged_commits["directories"],
                        push_num,
                        is_regression,
                    )

                    (
                        total_components_failures,
                        past_7_pushes_components_failures,
                        past_14_pushes_components_failures,
                        past_28_pushes_components_failures,
                        past_56_pushes_components_failures,
                    ) = get_and_update_past_failures(
                        "component",
                        task,
                        merged_commits["components"],
                        push_num,
                        is_regression,
                    )

                    if pushdate > HISTORY_DATE_START:
                        saved_nodes.add(i)

                        yield {
                            "revs": revisions,
                            "name": task,
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
                            "is_possible_regression": task in possible_regressions,
                            "is_likely_regression": task in likely_regressions,
                        }

            logger.info(f"saved push data nodes: {len(saved_nodes)}")
            logger.info(f"skipped {skipped_no_commits} (no commits in our DB)")
            logger.info(f"skipped {skipped_too_big_commits} (too big commits)")
            logger.info(f"skipped {skipped_no_tasks} (no interesting tasks)")

        db.append(test_scheduling.TEST_SCHEDULING_DB, generate_data())

        zstd_compress(test_scheduling.TEST_SCHEDULING_DB)

        past_failures["push_num"] = push_num
        past_failures.close()
        zstd_compress("data/past_failures.shelve")


def main():
    description = "Retrieve and extract the test scheduling history from ActiveData"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument(
        "op", help="Which operation to perform.", choices=["retrieve", "generate"]
    )

    args = parser.parse_args()

    retriever = Retriever()
    if args.op == "retrieve":
        retriever.retrieve_push_data()
    elif args.op == "generate":
        retriever.generate_test_scheduling_history()


if __name__ == "__main__":
    main()
