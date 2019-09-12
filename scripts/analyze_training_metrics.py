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
from typing import Any, Dict, Tuple

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy
from pandas import DataFrame
from scipy.signal import argrelextrema

LOGGER = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO)

# By default, if the latest metric point is 5% lower than the previous one, show a warning and exit
# with 1.
WARNING_THRESHOLD = 0.95
LOCAL_MIN_MAX_ORDER = 2

REPORT_METRICS = ["accuracy", "precision", "recall"]


def plot_graph(
    model_name: str,
    metric_name: str,
    df: DataFrame,
    title: str,
    output_directory: Path,
    file_path: str,
    metric_threshold: float,
) -> None:
    figure = plt.figure()
    axes = df.plot(y="value")
    axes.scatter(df.index, df["min"], c="r")
    axes.scatter(df.index, df["max"], c="g")
    # axes = plt.axes()

    # Formatting of the figure
    figure.autofmt_xdate()
    axes.fmt_xdata = mdates.DateFormatter("%Y-%m-%d-%H-%M")

    axes.set_title(title)

    # Display threshold
    axes.axhline(y=metric_threshold, linestyle="--", color="red")
    plt.annotate(
        "{:.4f}".format(metric_threshold),
        (df.index[-1], metric_threshold),
        textcoords="offset points",  # how to position the text
        xytext=(-10, 10),  # distance from text to points (x,y)
        ha="center",
        color="red",
    )

    # Display point values
    for single_x, single_y in zip(df.index, df.value):
        label = "{:.4f}".format(single_y)

        plt.annotate(
            label,
            (single_x, single_y),
            textcoords="offset points",
            xytext=(0, 10),
            ha="center",
        )

    output_file_path = output_directory.resolve() / file_path
    LOGGER.info("Saving %s figure", output_file_path)
    plt.savefig(output_file_path)

    plt.close(figure)


def parse_metric_file(metric_file_path: Path) -> Tuple[datetime, str, Dict[str, Any]]:
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

    return (date, model_name, metric)


def add_local_min_max_columns(df):
    df["min"] = df.iloc[
        argrelextrema(df.value.values, numpy.less_equal, order=LOCAL_MIN_MAX_ORDER)[0]
    ]["value"]
    df["max"] = df.iloc[
        argrelextrema(df.value.values, numpy.greater_equal, order=LOCAL_MIN_MAX_ORDER)[
            0
        ]
    ]["value"]


def analyze_metrics(
    metrics_directory: str,
    output_directory: str,
    warning_threshold: float,
    debug_regression: bool = False,
):
    root = Path(metrics_directory)

    metrics: Dict[str, Dict[str, Dict[datetime, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )

    threshold_ever_crossed = False

    # First process the metrics JSON files
    for metric_file_path in root.glob("metric*.json"):

        date, model_name, metric = parse_metric_file(metric_file_path)

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

    # Then analyze them
    for model_name in metrics:
        for metric_name, values in metrics[model_name].items():

            if metric_name.endswith("_std"):
                LOGGER.info(
                    "Skipping analysis of %r, analysis is not efficient on standard deviation",
                    metric_name,
                )
                continue

            df = DataFrame.from_dict(values, orient="index", columns=["value"])
            df = df.sort_index()

            add_local_min_max_columns(df)

            # Smooth the dataframe with rolling mean

            # The data-pipeline is scheduled to run every two weeks
            mean_df = df.rolling("31d").mean()

            add_local_min_max_columns(mean_df)

            if numpy.isnan(mean_df.iloc[-1]["min"]):
                LOGGER.info(
                    "Metric %r for model %s seems to be increasing",
                    metric_name,
                    model_name,
                )
            else:
                LOGGER.warning(
                    "Metric %r for model %s seems to be decreasing",
                    metric_name,
                    model_name,
                )

            # Compute the threshold for the metric
            if len(df.value) >= 2:
                before_last_value = df.value[-2]
            else:
                before_last_value = df.value[-1]
            metric_threshold = before_last_value * warning_threshold

            threshold_crossed = df.value[-1] < metric_threshold

            diff = (1 - warning_threshold) * 100

            if threshold_crossed:
                LOGGER.warning(
                    "Last metric %r for model %s is at least %f%% worse than the previous one",
                    metric_name,
                    model_name,
                    diff,
                )

                threshold_ever_crossed = threshold_ever_crossed or threshold_crossed

            # Plot the non-smoothed graph
            title = f"{model_name} {metric_name}"
            file_path = f"{model_name}_{metric_name}_before_smoothing.svg"

            plot_graph(
                model_name,
                metric_name,
                df,
                title,
                Path(output_directory),
                file_path,
                metric_threshold,
            )

            # Plot the smoothed graph
            title = f"Smoothed {model_name} {metric_name}"
            file_path = f"{model_name}_{metric_name}_after_smoothing.svg"

            plot_graph(
                model_name,
                metric_name,
                mean_df,
                title,
                Path(output_directory),
                file_path,
                metric_threshold,
            )

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
    parser.add_argument(
        "--warning_threshold",
        default=WARNING_THRESHOLD,
        type=float,
        help="If the last metric value is below the previous one*warning_threshold, fails. Default to 0.95",
    )
    parser.add_argument(
        "--debug-negative-slope",
        action="store_true",
        help="Should we display the linear regression detecting the global trend for debugging purposes",
    )

    args = parser.parse_args()

    analyze_metrics(
        args.metrics_directory,
        args.output_directory,
        args.warning_threshold,
        args.debug_negative_slope,
    )


if __name__ == "__main__":
    main()
