# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from copy import deepcopy

import pytest
from hackbot_agents.perf_regression.evidence import metric_key
from hackbot_agents.perf_regression.validation import evaluate


def points(value, count=20):
    return [{"job_id": i, "value": value + (i % 3 - 1) * 0.001} for i in range(count)]


def verdict(row, good=100, bad=102, candidate=100, count=20):
    key = metric_key(row["series_signature"])
    return evaluate(
        [row], {key: points(good)}, {key: points(bad)}, {key: points(candidate, count)}
    )


def test_recovery_requires_measurements_not_just_an_improvement(row):
    assert verdict(row)["fixed"]
    assert not verdict(row, candidate=101)["fixed"]
    assert not verdict(row, candidate=103)["fixed"]


def test_score_direction_is_reversed(row):
    row["series_signature"]["lower_is_better"] = False
    assert verdict(row, good=100, bad=98, candidate=100)["fixed"]
    assert not verdict(row, good=100, bad=98, candidate=97)["fixed"]


def test_recovery_requires_the_baselines_to_show_the_regression(row):
    result = verdict(row, good=100, bad=100, candidate=99)
    assert not result["fixed"]
    assert result["positive"]
    assert result["metrics"][0]["status"] == "inconclusive"


def test_rounded_constant_values_report_their_uncertainty(row):
    # Perfherder rounds to 0.01; identical rounded values are not certainty,
    # but the verdict follows the means.
    key = metric_key(row["series_signature"])

    def constant(value, count):
        return {key: [{"job_id": i, "value": value} for i in range(count)]}

    result = evaluate([row], constant(0.03, 2), constant(0.04, 2), constant(0.03, 5))
    assert result["fixed"]
    assert not result["metrics"][0]["statistically_significant"]


@pytest.mark.parametrize(
    "candidate,status",
    [(106, "improved"), (102.5, "improved"), (101.9, "fixed"), (100, "fixed")],
)
def test_recovering_most_of_the_regression_counts_as_back(row, candidate, status):
    # The regression is 10 (100 -> 110); 80% of it must be recovered.
    metric = verdict(row, good=100, bad=110, candidate=candidate)["metrics"][0]
    assert metric["status"] == status
    assert metric["recovered_fraction"] == pytest.approx(
        (110 - candidate) / 10, abs=0.01
    )


def test_noisy_recovery_is_reported_with_its_uncertainty(row):
    assert not verdict(row, count=1)["fixed"]
    key = metric_key(row["series_signature"])
    noisy = [{"job_id": i, "value": 90 + i} for i in range(20)]
    result = evaluate([row], {key: points(100)}, {key: points(102)}, {key: noisy})
    assert result["fixed"]
    assert not result["metrics"][0]["statistically_significant"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "100"])
def test_invalid_observation_cannot_pass(row, value):
    key = metric_key(row["series_signature"])
    result = evaluate(
        [row],
        {key: points(100)},
        {key: points(102)},
        {key: [{"job_id": i, "value": value} for i in range(20)]},
    )
    assert not result["fixed"]


@pytest.mark.parametrize(
    "good,bad,candidate,lower_is_better,status",
    [
        (1, 2, 0, True, "fixed"),
        (0, 2, 0, True, "fixed"),
        (0, 0, 0, True, "inconclusive"),
        (2, 0, 2, False, "fixed"),
        (0, 0, 2, False, "inconclusive"),
    ],
)
def test_zero_measurements_and_baselines(
    row, good, bad, candidate, lower_is_better, status
):
    fixed = status == "fixed"
    row["series_signature"]["lower_is_better"] = lower_is_better
    key = metric_key(row["series_signature"])
    groups = [
        {key: [{"job_id": i, "value": value} for i in range(5)]}
        for value in (good, bad, candidate)
    ]
    result = evaluate([row], *groups)
    metric = result["metrics"][0]
    assert result["fixed"] is fixed
    assert metric["status"] == status
    assert metric["counts"] == [5, 5, 5]
    assert metric["candidate_mean"] == candidate
    sign = 1 if lower_is_better else -1
    assert metric["improvement_pct"] == (
        pytest.approx(100 * sign * (bad - candidate) / bad) if bad else None
    )
    assert metric["residual_pct"] == (
        pytest.approx(100 * sign * (candidate - good) / good) if good else None
    )


def test_every_platform_and_configuration_must_recover(row):
    second = deepcopy(row)
    second["series_signature"]["extra_options"].append("no-nova")
    key = metric_key(row["series_signature"])
    other = metric_key(second["series_signature"])
    assert key != other
    result = evaluate(
        [row, second],
        {key: points(100), other: points(100)},
        {key: points(102), other: points(102)},
        {key: points(100)},
    )
    assert not result["fixed"]


def test_duplicate_jobs_do_not_inflate_sample_size(row):
    key = metric_key(row["series_signature"])
    repeated = [{"job_id": 1, "value": 100}] * 20
    assert not evaluate([row], {key: points(100)}, {key: points(102)}, {key: repeated})[
        "fixed"
    ]


@pytest.mark.parametrize("historical_count", [2, 4])
def test_historical_count_is_independent_of_candidate_rebuild(row, historical_count):
    key = metric_key(row["series_signature"])
    good, bad = (
        {key: points(100, historical_count)},
        {key: points(102, historical_count)},
    )
    assert evaluate([row], good, bad, {key: points(100, 5)})["fixed"]
    assert evaluate([row], good, bad, {key: points(100, 4)})["fixed"]


def test_single_historical_observation_is_inconclusive(row):
    key = metric_key(row["series_signature"])
    result = evaluate(
        [row], {key: points(100, 1)}, {key: points(102, 4)}, {key: points(100, 5)}
    )
    assert result["metrics"][0]["status"] == "inconclusive"
    metric = result["metrics"][0]
    assert metric["counts"] == [1, 4, 5]
    assert metric["candidate_mean"] == pytest.approx(100, abs=0.001)
    assert metric["improvement_pct"] == pytest.approx(1.96, abs=0.01)
    assert metric["residual_pct"] == pytest.approx(0, abs=0.01)
    assert result["positive"]
    assert not result["fixed"]
    assert "candidate_improvement_ci" not in metric


def test_missing_good_history_preserves_comparison_against_bad(row):
    key = metric_key(row["series_signature"])
    result = evaluate([row], {}, {key: points(102, 4)}, {key: points(100, 5)})
    metric = result["metrics"][0]
    assert metric["good_mean"] is None
    assert metric["residual_pct"] is None
    assert metric["counts"] == [0, 4, 5]
    assert metric["improvement_pct"] > 0
    assert result["positive"] and not result["fixed"]
