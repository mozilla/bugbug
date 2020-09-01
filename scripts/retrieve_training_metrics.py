# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import logging
import os
import sys
from os.path import abspath, join

import requests
import taskcluster

from bugbug.utils import get_taskcluster_options

ROOT_URI = "train_{}.per_date"
DATE_URI = "train_{}.per_date.{}"
BASE_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/{}/artifacts/public/metrics.json"
NAMESPACE_URI = "project.bugbug.{}"

LOGGER = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)


def get_task_metrics_from_uri(index_uri):
    index_url = BASE_URL.format(index_uri)
    LOGGER.info(f"Retrieving metrics from {index_url}")
    r = requests.get(index_url)

    if r.status_code == 404:
        LOGGER.error(f"File not found for URL {index_url}, check your arguments")
        sys.exit(1)

    r.raise_for_status()

    return r


def get_namespaces(index, index_uri):
    index_namespaces = index.listNamespaces(index_uri)

    return index_namespaces["namespaces"]


def is_later_or_equal(partial_date, from_date):
    for partial_date_part, from_date_part in zip(partial_date, from_date):
        if int(partial_date_part) > int(from_date_part):
            return True
        elif int(partial_date_part) < int(from_date_part):
            return False
        else:
            continue

    return True


def get_task_metrics_from_date(model, date, output_directory):
    options = get_taskcluster_options()

    index = taskcluster.Index(options)

    index.ping()

    # Split the date
    from_date = date.split(".")

    namespaces = []

    # Start at the root level
    # We need an empty list in order to append namespaces part to it
    namespaces.append([])

    # Recursively list all namespaces greater or equals than the given date
    while namespaces:
        current_ns = namespaces.pop()

        # Handle version level namespaces
        if not current_ns:
            ns_uri = ROOT_URI.format(model)
        else:
            current_ns_date = ".".join(current_ns)
            ns_uri = DATE_URI.format(model, current_ns_date)

        ns_full_uri = NAMESPACE_URI.format(ns_uri)

        tasks = index.listTasks(ns_full_uri)
        for task in tasks["tasks"]:
            task_uri = task["namespace"]
            r = get_task_metrics_from_uri(task_uri)

            # Write the file on disk
            file_name = f"metric_{'_'.join(task_uri.split('.'))}.json"
            file_path = abspath(join(output_directory, file_name))
            with open(file_path, "w") as metric_file:
                metric_file.write(r.text)
            LOGGER.info(f"Metrics saved to {file_path!r}")

        for namespace in get_namespaces(index, ns_full_uri):
            new_ns = current_ns.copy()
            new_ns.append(namespace["name"])

            if not is_later_or_equal(new_ns, from_date):
                LOGGER.debug("NEW namespace %s is before %s", new_ns, from_date)
                continue

            # Might not be efficient but size of `namespaces` shouldn't be too
            # big as we are doing a depth-first traversal
            if new_ns not in namespaces:
                namespaces.append(new_ns)


def main():
    description = "Retrieve a model training metrics"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument(
        "-d",
        "--output-directory",
        default=os.getcwd(),
        help="In which directory the script should save the metrics file. The directory must exists",
    )
    parser.add_argument("model", help="Which model to retrieve training metrics from.")
    parser.add_argument(
        "date",
        nargs="?",
        help="Which date should we retrieve training metrics from. Default to latest",
    )

    args = parser.parse_args()

    get_task_metrics_from_date(args.model, args.date, args.output_directory)


if __name__ == "__main__":
    main()
