# -*- coding: utf-8 -*-

import argparse
import logging
import sys
from os.path import abspath

import requests

LATEST_URI = "train_{}.latest"
VERSIONED_URI = "train_{}.{}"
DATED_VERSIONED_URI = "train_{}.{}.{}"
BASE_URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.{}/artifacts/public/metrics.json"

LOGGER = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)


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

    if not args.version:
        index_uri = LATEST_URI.format(args.model)
    elif not args.date:
        index_uri = VERSIONED_URI.format(args.model, args.version)
    else:
        index_uri = DATED_VERSIONED_URI.format(args.model, args.version, args.date)

    index_url = BASE_URL.format(index_uri)
    LOGGER.info(f"Retrieving metrics from {index_url}")
    r = requests.get(index_url)

    if r.status_code == 404:
        LOGGER.error(f"File not found for URL {index_url}, check your arguments")
        sys.exit(1)

    r.raise_for_status()

    if args.output:
        file_path = abspath(args.output)
        with open(file_path, "w") as output_file:
            output_file.write(r.text)
        LOGGER.info(f"Metrics saved to {file_path!r}")
    else:
        print(r.text)


if __name__ == "__main__":
    main()
