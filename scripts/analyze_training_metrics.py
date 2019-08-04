# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.
""" Given a directory containing training metrics, generate SVF graphs and check that the metrics are not getting worse than before.
"""

import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import matplotlib.dates as mdates
import matplotlib.pyplot as plt

LOGGER = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)

# If the latest metric point is 5% lower than the previous one, show a warning and exit with 1.
WARNING_THRESHOLD = 0.95

REPORT_METRICS = ["accuracy", "precision", "recall"]


def plot_graph(
    model_name: str,
    metric_name: str,
    values_dict: Dict[datetime, float],
    output_directory: Path,
) -> bool:
    sorted_metrics = sorted(values_dict.items())
    x, y = zip(*sorted_metrics)

    # Compute the threshold
    if len(y) >= 2:
        before_last_value = y[-2]
    else:
        before_last_value = y[-1]
    metric_threshold = before_last_value * WARNING_THRESHOLD

    figure = plt.figure()
    axes = plt.axes()

    # Formatting of the figure
    figure.autofmt_xdate()
    axes.fmt_xdata = mdates.DateFormatter("%Y-%m-%d-%H-%M")
    axes.set_title(f"{model_name} {metric_name}")

    # Display threshold
    axes.axhline(y=metric_threshold, linestyle="--", color="red")
    plt.annotate(
        "{:.4f}".format(metric_threshold),
        (x[-1], metric_threshold),
        textcoords="offset points",  # how to position the text
        xytext=(-10, 10),  # distance from text to points (x,y)
        ha="center",
        color="red",
    )

    # Display point values
    for single_x, single_y in zip(x, y):
        label = "{:.4f}".format(single_y)

        plt.annotate(
            label,
            (single_x, single_y),
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
        )

    axes.plot_date(x, y, marker=".", fmt="-")

    output_file_path = output_directory.resolve() / f"{model_name}_{metric_name}.svg"
    LOGGER.info("Saving %s figure", output_file_path)
    plt.savefig(output_file_path)

    plt.close(figure)

    # Check if the threshold has been crossed
    return y[-1] < metric_threshold


def analyze_metrics(metrics_directory: str, output_directory: str):
    root = Path(metrics_directory)

    metrics: Dict[str, Dict[str, Dict[datetime, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )

    threshold_ever_crossed = False

    for metric_file_path in root.glob("metric*.json"):

        # Load the metric
        with open(metric_file_path, "r") as metric_file:
            metric = json.load(metric_file)

        # Get the model, date and version from the file
        # TODO: Might be better storing it in the file
        file_path_parts = metric_file_path.stem.split("_")

        assert file_path_parts[:5] == ["metric", "project", "relman", "bugbug", "train"]
        model_name = file_path_parts[5]
        assert file_path_parts[6:8] == ["per", "date"]
        date_parts = list(map(int, file_path_parts[8:14]))
        date = datetime(
            date_parts[0],
            date_parts[1],
            date_parts[2],
            date_parts[3],
            date_parts[4],
            date_parts[5],
            tzinfo=timezone.utc,
        )
        # version = file_path_parts[14:]  # TODO: Use version

        # Then process the report
        for key, value in metric["report"]["average"].items():
            if key not in REPORT_METRICS:
                continue

            metrics[model_name][key][date] = value

        # Also process the test_* metrics
        for key, value in metric.items():
            if not key.startswith("test_"):
                continue

            metrics[model_name][f"{key}_mean"][date] = value["mean"]
            metrics[model_name][f"{key}_std"][date] = value["std"]

    for model_name in metrics:
        for metric_name, values in metrics[model_name].items():
            threshold_crossed = plot_graph(
                model_name, metric_name, values, Path(output_directory)
            )

            if threshold_crossed:
                LOGGER.warning(
                    "Last metric %r for model %s is 5%% worse than the previous one",
                    metric_name,
                    model_name,
                )

                threshold_ever_crossed = threshold_ever_crossed or threshold_crossed

    if threshold_ever_crossed:
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "metrics_directory",
        metavar="metrics-directory",
        help="In which directory the script can find the metrics JSON files",
    )
    parser.add_argument(
        "output_directory",
        metavar="output-directory",
        help="In which directory the script will save the generated graphs",
    )

    args = parser.parse_args()

    analyze_metrics(args.metrics_directory, args.output_directory)


if __name__ == "__main__":
    main()
