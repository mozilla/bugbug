# -*- coding: utf-8 -*-

import argparse
import json
import os
import subprocess
import tarfile
from datetime import datetime
from logging import INFO, basicConfig, getLogger

import dateutil.parser
import requests
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

from bugbug import db, repository, test_scheduling
from bugbug.utils import ExpQueue, download_check_etag, zstd_compress

basicConfig(level=INFO)
logger = getLogger(__name__)

JOBS_TO_CONSIDER = ("test-", "build-")


URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_test_scheduling_history.latest/artifacts/public/adr_cache.tar.xz"

TRAINING_MONTHS = 6


class Retriever(object):
    def retrieve_test_scheduling_history(self):
        os.makedirs("data", exist_ok=True)

        # Download previous cache.
        cache_path = os.path.abspath("data/adr_cache")
        if not os.path.exists(cache_path):
            try:
                download_check_etag(URL, "adr_cache.tar.xz")
                with tarfile.open("adr_cache.tar.xz", "r:xz") as tar:
                    tar.extractall()
                assert os.path.exists("data/adr_cache"), "Decompressed adr cache exists"
            except requests.exceptions.HTTPError:
                logger.info("The adr cache is not available yet")

        # Setup adr cache configuration.
        os.makedirs(os.path.expanduser("~/.config/adr"), exist_ok=True)
        with open(os.path.expanduser("~/.config/adr/config.toml"), "w") as f:
            f.write(
                f"""[adr.cache.stores]
file = {{ driver = "file", path = "{cache_path}" }}
            """
            )

        # Get the commits DB.
        if db.is_old_version(repository.COMMITS_DB) or not db.exists(
            repository.COMMITS_DB
        ):
            db.download(repository.COMMITS_DB, force=True)

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
                "today-2day",
                "--branch",
                "autoland",
            ],
            check=True,
            stdout=subprocess.DEVNULL,  # Redirect to /dev/null, as the logs are too big otherwise.
        )

        HISTORY_DATE_START = datetime.now() - relativedelta(months=TRAINING_MONTHS)

        with open("push_data.json", "r") as f:
            data = json.load(f)

        push_data = {}
        for row in data[1:]:
            # Revision -> (all tasks, possible regressions, likely regressions)
            push_data[row[0]] = (row[1], row[2], row[3])

        HISTORICAL_TIMESPAN = 56

        past_failures = {}

        def get_past_failures(task, push_num):
            if task not in past_failures:
                past_failures[task] = ExpQueue(push_num, HISTORICAL_TIMESPAN + 1, 0)

            return past_failures[task][push_num]

        def generate_data():
            commits_with_data = set()
            saved_nodes = set()

            push_num = 0
            for commit_data in tqdm(repository.get_commits()):
                node = commit_data["node"]

                if node not in push_data:
                    continue

                commits_with_data.add(node)

                commit_push_data = push_data[node]

                for task in commit_push_data[0]:
                    if not any(task.startswith(j) for j in JOBS_TO_CONSIDER):
                        continue

                    total_failures = get_past_failures(task, push_num)
                    past_7_pushes_failures = total_failures - get_past_failures(
                        task, push_num - 7
                    )
                    past_14_pushes_failures = total_failures - get_past_failures(
                        task, push_num - 14
                    )
                    past_28_pushes_failures = total_failures - get_past_failures(
                        task, push_num - 28
                    )
                    past_56_pushes_failures = total_failures - get_past_failures(
                        task, push_num - 56
                    )

                    pushdate = dateutil.parser.parse(commit_data["pushdate"])
                    if pushdate > HISTORY_DATE_START:
                        saved_nodes.add(node)

                        yield {
                            "rev": node,
                            "name": task,
                            "failures": total_failures,
                            "failures_past_7_pushes": past_7_pushes_failures,
                            "failures_past_14_pushes": past_14_pushes_failures,
                            "failures_past_28_pushes": past_28_pushes_failures,
                            "failures_past_56_pushes": past_56_pushes_failures,
                            "is_possible_regression": task in commit_push_data[1],
                            "is_likely_regression": task in commit_push_data[2],
                        }

                    if task in commit_push_data[1] or task in commit_push_data[2]:
                        past_failures[task][push_num] = total_failures + 1

                push_num += 1

            logger.info(f"push data nodes: {len(push_data)}")

            logger.info(f"commits linked to push data: {len(commits_with_data)}")

            logger.info(f"saved push data nodes: {len(saved_nodes)}")

        db.write(test_scheduling.TEST_SCHEDULING_DB, generate_data())

        zstd_compress(test_scheduling.TEST_SCHEDULING_DB)

        with tarfile.open("data/adr_cache.tar.xz", "w:xz") as tar:
            tar.add("data/adr_cache")


def main():
    description = "Retrieve and extract the test scheduling history from ActiveData"
    parser = argparse.ArgumentParser(description=description)

    # Parse args to show the help if `--help` is passed
    parser.parse_args()

    retriever = Retriever()
    retriever.retrieve_test_scheduling_history()


if __name__ == "__main__":
    main()
