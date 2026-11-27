# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest.mock import AsyncMock, Mock

import pytest
from hackbot_agents.perf_regression.evidence import EvidenceError, metric_key
from hackbot_agents.perf_regression.workflow import run_attempts
from test_validation import points


def dependencies(row, candidates):
    key = metric_key(row["series_signature"])
    return {
        "propose": AsyncMock(
            return_value={
                "has_candidate": True,
                "summary": "Cache repeated grid measurements",
            }
        ),
        "historical_baselines": AsyncMock(
            return_value=({key: points(100)}, {key: points(102)})
        ),
        "measure": AsyncMock(
            side_effect=[{"measurements": {key: points(n)}} for n in candidates]
        ),
        "checkpoint": Mock(),
    }


async def test_stops_at_first_verified_fix(row):
    deps = dependencies(row, [100, 101])
    result = await run_attempts([row], max_attempts=1, **deps)
    assert result["status"] == "fixed"
    assert result["winning_attempt"] == 1
    deps["propose"].assert_awaited_once()
    deps["measure"].assert_awaited_once()
    deps["historical_baselines"].assert_awaited_once()
    assert len(deps["propose"].call_args.args[1]) == 1


async def test_hard_limit_one_candidate_even_when_another_could_recover(row):
    deps = dependencies(row, [101, 100])
    result = await run_attempts([row], max_attempts=1, **deps)
    assert result["status"] == "exhausted"
    deps["propose"].assert_awaited_once()
    deps["measure"].assert_awaited_once()
    assert "winning_attempt" not in result


async def test_no_solution_does_not_spend_ci(row):
    deps = dependencies(row, [])
    deps["propose"].return_value = {
        "has_candidate": False,
        "summary": "No plausible source fix",
    }
    result = await run_attempts([row], max_attempts=1, **deps)
    assert result["status"] == "no_solution"
    deps["historical_baselines"].assert_not_awaited()
    deps["measure"].assert_not_awaited()


async def test_infrastructure_failure_does_not_become_success_or_retry_push(row):
    deps = dependencies(row, [])
    deps["measure"].side_effect = EvidenceError("CI timed out")
    with pytest.raises(EvidenceError):
        await run_attempts([row], max_attempts=1, **deps)
    assert deps["measure"].await_count == 1


@pytest.mark.parametrize("max_attempts", [0, 2, 3, 4])
async def test_cannot_raise_attempt_budget(row, max_attempts):
    deps = dependencies(row, [])
    with pytest.raises(ValueError):
        await run_attempts([row], max_attempts=max_attempts, **deps)
    deps["propose"].assert_not_awaited()


async def test_build_failure_is_reported_without_another_push(row):
    deps = dependencies(row, [100])
    key = metric_key(row["series_signature"])
    deps["measure"].side_effect = [
        {
            "failure_reason": "CI build/test failure",
            "failed_jobs": [{"task_id": "failed"}],
            "measurements": {},
        },
        {"measurements": {key: points(100)}},
    ]
    result = await run_attempts([row], max_attempts=1, **deps)
    assert result["status"] == "exhausted"
    deps["measure"].assert_awaited_once()
    deps["propose"].assert_awaited_once()
    assert (
        result["attempts"][0]["validation"]["failure_reason"] == "CI build/test failure"
    )


async def test_inconclusive_history_does_not_prevent_candidate_push(row):
    deps = dependencies(row, [100])
    key = metric_key(row["series_signature"])
    deps["historical_baselines"].return_value = (
        {key: points(100, 4)},
        {key: points(100, 4)},
    )
    result = await run_attempts([row], max_attempts=1, **deps)
    deps["measure"].assert_awaited_once()
    assert result["status"] == "exhausted"
    # Equal baselines do not show the regression, so nothing can be verified.
    assert result["attempts"][0]["validation"]["metrics"][0]["status"] == "inconclusive"


async def test_four_historical_samples_allow_candidate_validation(row):
    deps = dependencies(row, [100])
    key = metric_key(row["series_signature"])
    deps["historical_baselines"].return_value = (
        {key: points(100, 4)},
        {key: points(102, 4)},
    )
    result = await run_attempts([row], max_attempts=1, **deps)
    deps["measure"].assert_awaited_once()
    assert result["status"] == "fixed"


async def test_partial_improvements_are_preserved_after_one_attempt(row):
    deps = dependencies(row, [101, 101.5, 102.5])
    result = await run_attempts([row], max_attempts=1, **deps)
    assert result["status"] == "exhausted"
    assert result["positive_attempts"] == [1]
    deps["measure"].assert_awaited_once()
    assert result["attempts"][0]["status"] == "improved"


async def test_mean_recovery_stops_without_significance_gate(row):
    deps = dependencies(row, [100.2])
    result = await run_attempts([row], max_attempts=1, **deps)
    assert result["status"] == "fixed"
    assert deps["measure"].await_count == 1


async def test_failed_ci_preserves_positive_measurements_without_claiming_recovery(row):
    deps = dependencies(row, [])
    key = metric_key(row["series_signature"])
    deps["measure"].return_value = {
        "failure_reason": "CI build/test failure",
        "measurements": {key: points(100, 4)},
    }
    deps["measure"].side_effect = None
    result = await run_attempts([row], max_attempts=1, **deps)
    assert result["status"] == "exhausted"
    assert result["positive_attempts"] == [1]
    assert "winning_attempt" not in result
    assert not result["attempts"][0]["validation"]["fixed"]
    assert result["attempts"][0]["validation"]["metrics"][0]["improvement_pct"] > 0
