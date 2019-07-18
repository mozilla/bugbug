# -*- coding: utf-8 -*-

import argparse
import logging
import sys
from os.path import abspath

import requests
import taskcluster

from bugbug.utils import get_taskcluster_options

LATEST_URI = "train_{}.latest"
VERSIONED_URI = "train_{}.{}"
DATED_VERSIONED_URI = "train_{}.{}.{}"
BASE_URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.{}/artifacts/public/metrics.json"
NAMESPACE_URI = "project.relman.bugbug.{}"

LOGGER = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)


def get_single_task_metrics(model, version=None, date=None):
    if not version:
        index_uri = LATEST_URI.format(model)
    elif not date:
        index_uri = VERSIONED_URI.format(model, version)
    else:
        index_uri = DATED_VERSIONED_URI.format(model, version, date)

    index_url = BASE_URL.format(index_uri)
    LOGGER.info(f"Retrieving metrics from {index_url}")
    r = requests.get(index_url)

    if r.status_code == 404:
        LOGGER.error(f"File not found for URL {index_url}, check your arguments")
        sys.exit(1)

    r.raise_for_status()

    return r


def get_namespaces(index, model, version, date):
    namespaces = []

    # Temporary workaround
    versions = [
        "v0.0.52",
        "v0.0.55",
        "v0.0.56",
        "v0.0.57",
        "v0.0.60",
        "v0.0.62",
        "v0.0.64",
        "v0.0.65",
        "v0.0.66",
        "v0.0.67",
        "v0.0.68",
        "v0.0.69",
    ]

    for version in versions:
        index_uri = DATED_VERSIONED_URI.format(model, version, date)
        index_uri = NAMESPACE_URI.format(index_uri)

        index_namespaces = index.listNamespaces(index_uri)

        namespaces.extend(index_namespaces["namespaces"])

    return namespaces


def is_later_or_equal(partial_date, from_date):
    for partial_date_part, from_date_part in zip(partial_date, from_date):
        if int(partial_date_part) > int(from_date_part):
            return True
        elif int(partial_date_part) < int(from_date_part):
            return False
        else:
            continue

    return True


def get_task_metrics_from_date(model, version, date):
    options = get_taskcluster_options()

    index = taskcluster.Index(options)

    index.ping()

    # Split the date
    from_date = date.split(".")

    uris = []
    # Start at the top level
    uris.append([from_date[0]])

    # Recursively list all namespaces greater or equals than the given date
    while uris:
        uri = uris.pop(0)

        for namespace in get_namespaces(index, model, version, ".".join(uri)):
            new_uri = uri.copy()
            new_uri.append(namespace["name"])

            if not is_later_or_equal(new_uri, from_date):
                print("NEW URI is before from_date", new_uri, from_date)
                continue

            # Temp
            if new_uri not in uris:
                uris.append(new_uri)
            print("NAMESPACE", namespace)

        print("URIS", uris)


def main():
    description = "Retrieve a model training metrics"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("model", help="Which model to retrieve training metrics from.")
    parser.add_argument(
        "version",
        nargs="?",
        help="Which bugbug version should we retrieve training metrics from.",
        default=None,
    )
    parser.add_argument(
        "date",
        nargs="?",
        help="Which date should we retrieve training metrics from. Default to latest",
        default=None,
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Where to output the metrics.json file. Default to printing its content",
        default=None,
    )

    args = parser.parse_args()

    if False:
        r = get_single_task_metrics(args.model, args.version, args.date)
    else:
        r = get_task_metrics_from_date(args.model, args.version, args.date)

    if args.output:
        file_path = abspath(args.output)
        with open(file_path, "w") as output_file:
            output_file.write(r.text)
        LOGGER.info(f"Metrics saved to {file_path!r}")
    else:
        print(r.text)


if __name__ == "__main__":
    main()
