# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pytest

from bugbug import db
from bugbug.perf import calibration, datasets, label_policy

UTC = timezone.utc


def _t(day, hour=0):
    return datetime(2026, 5, day, hour, tzinfo=UTC)


def test_calibration_applies_breakpoints_and_round_trips() -> None:
    cal = calibration.Calibration("v1", (0.1, 0.5, 0.9), (0.01, 0.05, 0.25), "t")
    # Linear between breakpoints, the nearest end outside them.
    assert cal.apply(0.3) == pytest.approx(0.03)
    assert cal.apply(0.0) == 0.01 and cal.apply(1.0) == 0.25
    record = cal.to_record()
    assert record["method"] == "isotonic" and record["fitted_at"] == "t"
    assert calibration.Calibration.from_record(record) == cal
    assert calibration.load_calibrations([record]) == {"v1": cal}


def test_fit_calibration_is_monotone_and_follows_the_rates() -> None:
    rng = np.random.default_rng(0)
    scores = rng.uniform(0, 1, 5000)
    # The true culprit rate is the cube of the raw score.
    labels = (rng.uniform(0, 1, 5000) < scores**3).astype(int)
    cal = calibration.fit_calibration(scores, labels, "v1", "2026-10-30T00:00:00")
    assert cal.model_version == "v1" and cal.fitted_at == "2026-10-30T00:00:00"
    assert list(cal.x) == sorted(cal.x) and list(cal.y) == sorted(cal.y)
    for score in (0.2, 0.5, 0.7, 0.9):
        assert cal.apply(score) == pytest.approx(score**3, abs=0.06)

    from sklearn.isotonic import IsotonicRegression

    # The breakpoints reproduce scikit-learn's fit over the same bins.
    order = np.argsort(scores, kind="stable")
    bins = np.array_split(order, len(order) // calibration.BIN_SIZE)
    isotonic = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip")
    isotonic.fit(
        [scores[b].mean() for b in bins],
        [labels[b].mean() for b in bins],
        sample_weight=[len(b) for b in bins],
    )
    probe = np.linspace(0, 1, 101)
    assert [cal.apply(s) for s in probe] == pytest.approx(isotonic.predict(probe))


def test_fit_calibration_top_step_rests_on_a_whole_bin() -> None:
    rng = np.random.default_rng(4)
    scores = rng.uniform(0, 1, 2000)
    # Only the single highest-scoring commit is a culprit: on its own it would
    # map the top of the scale to 1.0, binned it is one culprit in fifty.
    labels = np.zeros(2000, dtype=int)
    labels[np.argmax(scores)] = 1
    cal = calibration.fit_calibration(scores, labels, "v1")
    assert cal.apply(1.0) == pytest.approx(1 / calibration.BIN_SIZE)
    assert cal.apply(0.0) == 0.0


def test_reliability_and_ranking_metrics() -> None:
    metrics = calibration.reliability_metrics([0.1, 0.9, 0.9, 0.1], [0, 1, 1, 0])
    assert metrics["brier"] == pytest.approx(0.01)
    assert metrics["log_loss"] == pytest.approx(-math.log(0.9))
    assert metrics["ece"] == pytest.approx(0.1)

    # 100 commits, the 25 culprits all at the top score.
    ranking = calibration.ranking_metrics([0.2, 0.9, 0.5, 0.1] * 25, [0, 1, 0, 0] * 25)
    assert ranking["precision_at_1_percent"] == 1.0
    assert ranking["recall_at_1_percent"] == pytest.approx(1 / 25)
    assert ranking["precision_at_5_percent"] == 1.0
    assert ranking["recall_at_5_percent"] == pytest.approx(5 / 25)
    assert calibration.ranking_metrics([0.5], [0])["recall_at_2_percent"] is None


def _regression(push_id, day, culprits, outcome="concluded"):
    return {
        "summary_id": push_id,
        "outcome": outcome,
        "push_id": push_id,
        "push_time": _t(day).isoformat(),
        "culprit_commits": culprits,
    }


def _status(push_id, day, nodes, label_state="clean", settled=True):
    return {
        "push_id": push_id,
        "push_time": _t(day).isoformat(),
        "label_state": label_state,
        "settled": settled,
        "commits": [
            {
                "node": node,
                "bug_id": 1,
                "backout": node.startswith("r"),
                "landing_id": f"{push_id}:1",
                "landing_size": 1,
            }
            for node in nodes
        ],
    }


def test_label_scored_commits() -> None:
    regressions = [
        _regression(1, 1, ["c1"]),
        _regression(2, 2, []),
        _regression(5, 5, ["x1"], outcome="rejected"),
        _regression(7, 7, ["gone"]),
    ]
    statuses = [
        _status(1, 1, ["c1", "d1"], "culprit"),
        _status(2, 2, ["u1"], "culprit"),
        _status(3, 3, ["r1", "n1", "m1"]),
        _status(4, 4, ["p1"], "pending"),
        _status(5, 5, ["x1"], "rejected"),
        _status(6, 6, ["old"], settled=False),
    ]
    scores = {
        "c1": 0.7,
        "d1": 0.2,
        "u1": 0.4,
        "n1": 0.1,
        "r1": 0.3,
        "p1": 0.5,
        "x1": 0.6,
        "old": 0.2,
        "gone": 0.9,
    }
    rows = calibration.label_scored_commits(regressions, statuses, scores)
    # Pinned culprits are positives, even when their push status is missing;
    # settled clean commits, backouts included, are negatives. The innocent
    # commit of a culprit push, the unpinned push, pending and rejected pushes,
    # unsettled pushes and the unscored commit m1 are left out.
    assert [(row.node, row.label, row.score) for row in rows] == [
        ("c1", 1, 0.7),
        ("n1", 0, 0.1),
        ("r1", 0, 0.3),
        ("gone", 1, 0.9),
    ]
    assert rows[0].time == _t(1)


def _rows(count, seed, days):
    rng = np.random.default_rng(seed)
    rows = []
    for i in range(count):
        score = float(rng.uniform())
        # Raw scores overstate the risk: the true rate is half their square.
        label = int(rng.uniform() < 0.5 * score**2)
        time = _t(1) + timedelta(days=days * i / count)
        rows.append(calibration.LabeledScore(f"n{i}", time, score, label))
    rows.sort(key=lambda row: (row.time, row.node))
    return rows


def test_build_calibration_record_and_holdout() -> None:
    rows = _rows(400, 1, 120)
    assert (
        calibration.build_calibration_record(rows, "v1", min_positives=10_000) is None
    )

    record = calibration.build_calibration_record(
        rows, "v1", holdout_days=30, min_positives=10, fitted_at=_t(31)
    )
    assert record is not None
    positives = sum(row.label for row in rows)
    assert record["model_version"] == "v1" and record["method"] == "isotonic"
    assert record["commits"] == 400 and record["positives"] == positives
    assert record["base_rate"] == pytest.approx(positives / 400)
    assert record["fit_window"] == {
        "since": rows[0].time.isoformat(),
        "until": rows[-1].time.isoformat(),
    }
    assert record["fitted_at"] == "2026-05-31T00:00:00+00:00"
    assert record["bin_size"] == calibration.BIN_SIZE
    assert record["policy_version"] == label_policy.POLICY_VERSION

    holdout = record["holdout"]
    cutoff = rows[-1].time - timedelta(days=30)
    assert holdout["commits"] == sum(row.time >= cutoff for row in rows)
    assert (
        holdout["since"] == next(r.time for r in rows if r.time >= cutoff).isoformat()
    )
    assert (
        set(holdout["raw"])
        == {"log_loss", "brier", "ece"}
        == set(holdout["calibrated"])
    )
    assert set(holdout["ranking"]) == {
        f"{metric}_at_{percent}_percent"
        for metric in ("precision", "recall")
        for percent in (1, 2, 5)
    }
    # Calibrated probabilities fit the observed rates better than raw scores.
    assert holdout["calibrated"]["log_loss"] < holdout["raw"]["log_loss"]
    assert holdout["calibrated"]["ece"] < holdout["raw"]["ece"]

    cal = calibration.load_calibrations([record])["v1"]
    assert cal.apply(0.95) > cal.apply(0.05)
    assert max(cal.y) <= 1.0 and min(cal.y) >= 0.0


def test_build_calibration_record_without_holdout_culprits() -> None:
    rows = _rows(60, 3, 10)
    # Every row inside the holdout: no older rows to fit a trial mapping on.
    record = calibration.build_calibration_record(
        rows, "v1", holdout_days=365, min_positives=1
    )
    assert record is not None and record["holdout"] is None


def test_scores_by_version() -> None:
    records: list[dict[str, Any]] = [
        {"node": "a", "risk_score": 0.1, "model_version": "v1"},
        {"node": "b", "risk_score": None, "model_version": "v1"},
        {"node": "a", "risk_score": 0.3, "model_version": "v2"},
    ]
    assert calibration.scores_by_version(records) == {
        "v1": {"a": 0.1},
        "v2": {"a": 0.3},
    }


def test_calibrate_command_writes_one_record_per_calibratable_version() -> None:
    from scripts import perf_alerts_retriever as retriever

    rng = np.random.default_rng(2)
    statuses, regressions, risk_records = [], [], []
    v2_positives = 0
    for i in range(300):
        node = f"{i:040x}"
        time = _t(1 + i // 20).isoformat()
        score = float(rng.uniform())
        culprit = bool(rng.uniform() < 0.3 * score)
        statuses.append(
            {
                "push_id": i,
                "push_time": time,
                "label_state": "culprit" if culprit else "clean",
                "settled": True,
                "commits": [
                    {
                        "node": node,
                        "bug_id": 1,
                        "backout": False,
                        "landing_id": f"{i}:1",
                        "landing_size": 1,
                    }
                ],
            }
        )
        if culprit:
            regressions.append(
                _regression(i, 1 + i // 20, [node]) | {"outcome": "attributed"}
            )
        risk_records.append(
            {
                "node": node,
                "push_id": i,
                "push_time": time,
                "risk_score": score,
                "ignored_reason": None,
                "model_version": "v1",
            }
        )
        # The second model scored the first ten commits only.
        if i < 10:
            v2_positives += culprit
            risk_records.append({**risk_records[-1], "model_version": "v2"})
    db.write(datasets.PERF_REGRESSIONS_DB, regressions)
    db.write(datasets.PERF_PUSH_STATUS_DB, statuses)
    db.write(datasets.PERF_COMMIT_RISK_DB, risk_records)

    retriever.Retriever().calibrate(settle_days=21, holdout_days=5, min_positives=5)

    records = {r["model_version"]: r for r in datasets.get_perf_risk_calibration()}
    assert ("v2" in records) == (v2_positives >= 5)
    v1 = records["v1"]
    assert v1["commits"] == 300 and v1["positives"] == len(regressions)
    assert v1["holdout"] is not None and v1["holdout"]["commits"] > 0
    assert calibration.Calibration.from_record(v1).apply(0.9) > 0.1
