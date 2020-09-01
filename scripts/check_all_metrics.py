# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import logging
import os
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from typing import List

import taskcluster

from bugbug.utils import get_taskcluster_options

LOGGER = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)

QUEUE_ROUTE_PATTERN = "index.project.bugbug.train_*.per_date.*"

CURRENT_DIR = Path(__file__).resolve().parent


def download_metric(model_name: str, metric_directory: str):
    download_script_path = "bugbug-retrieve-training-metrics"

    cli_args: List[str] = [
        download_script_path,
        model_name,
        "2019",
        "-d",
        metric_directory,
    ]

    LOGGER.info("Download metrics for %r", model_name)

    subprocess.run(cli_args, check=True)


def check_metrics(metric_directory: str, output_directory: str):
    analyze_script_path = "bugbug-analyze-training-metrics"

    cli_args: List[str] = [analyze_script_path, metric_directory, output_directory]

    LOGGER.info("Checking metrics")

    subprocess.run(cli_args, check=True)


def get_model_name(queue, task_id: str):
    dependency_task = queue.task(task_id)

    # Check the route to detect training tasks
    for route in dependency_task["routes"]:
        if fnmatch(route, QUEUE_ROUTE_PATTERN):
            model_name = route.split(".")[4]  # model_name = "train_component"
            return model_name[6:]

    # Show a warning if no matching route was found, this can happen when the
    # current task has a dependency to a non-training task or if the route
    # pattern changes.
    LOGGER.warning(f"No matching route found for task id {task_id}")


def get_model_names(task_id: str) -> List[str]:
    options = get_taskcluster_options()
    queue = taskcluster.Queue(options)
    task = queue.task(task_id)

    model_names = []

    for i, task_id in enumerate(task["dependencies"]):
        LOGGER.info(
            "Loading task dependencies {}/{} {}".format(
                i + 1, len(task["dependencies"]), task_id
            )
        )

        model_name = get_model_name(queue, task_id)

        if model_name:
            LOGGER.info("Adding model %r to download list", model_name)
            model_names.append(model_name)

    return model_names


def main():
    description = "Get all the metrics name from taskcluster dependency, download them and check them"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument(
        "metric_directory",
        metavar="metric-directory",
        help="Which directory to download metrics to",
    )
    parser.add_argument(
        "output_directory",
        metavar="output-directory",
        help="Which directory to output graphs to",
    )

    parser.add_argument(
        "--task-id",
        type=str,
        default=os.environ.get("TASK_ID"),
        help="Taskcluster task id to analyse",
    )

    args = parser.parse_args()

    model_names = get_model_names(args.task_id)

    for model in model_names:
        download_metric(model, args.metric_directory)

    check_metrics(args.metric_directory, args.output_directory)


if __name__ == "__main__":
    main()
