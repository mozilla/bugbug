# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from bugbug.perf import extraction
from bugbug.perf import simulation as sim
from bugbug.perf.calibration import Calibration
from bugbug.perf.simulation import strategies

UTC = timezone.utc
START = datetime(2026, 5, 1, tzinfo=UTC)
SUITE = frozenset(
    f"test-linux2404-64-shippable/opt-browsertime-tp6-firefox-p{k}" for k in range(300)
)
L1 = "test-linux2404-64-shippable/opt-talos-g1"
L2 = "test-linux2404-64-shippable/opt-awsy-base"
FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "perf_simulation_replay.json"
)


def make_timeline(
    count,
    step_hours=1.0,
    pushlog_start=100,
    dontbuild=(),
    risks=None,
    regressions=(),
    full=(0,),
    times=None,
):
    """A synthetic timeline: ``count`` pushes, one commit each, full suite on ``full``."""
    pushes = []
    for i in range(count):
        risk = (risks or {}).get(i, 0.05)
        commit = sim.Commit(f"{i:040x}", risk=risk, ignored_reason=None)
        if risk is None:
            commit = sim.Commit(f"{i:040x}", risk=None, ignored_reason="merge")
        pushes.append(
            sim.Push(
                index=i,
                push_id=1000 + i,
                pushlog_id=pushlog_start + i,
                time=times[i] if times else START + timedelta(hours=step_hours * i),
                commits=(commit,),
                dontbuild=i in dontbuild,
                ran_labels=SUITE | {L1, L2} if i in full else frozenset(),
            )
        )
    regs = [
        sim.Regression(
            summary_id, pushes[index].index, pushes[index].time, frozenset(labels)
        )
        for summary_id, index, labels in regressions
    ]
    return sim.Timeline(pushes, regs)


def batches(strategy, timeline):
    return sim.replay(strategy, timeline).batch_indices


def test_fixed_interval_and_dontbuild() -> None:
    timeline = make_timeline(16, pushlog_start=100, dontbuild=(10,))
    # The batch due on the DONTBUILD push 110 is deferred to 111, and the
    # count restarts from there.
    assert batches(sim.FixedInterval(5), timeline) == [0, 5, 11]
    assert sim.replay(sim.FixedInterval(5), timeline).decisions[10] is None


def test_commit_count() -> None:
    # One commit per push: a batch every 4 commits is a batch every 4 pushes.
    timeline = make_timeline(12)
    assert batches(sim.CommitCount(commits=4), timeline) == [0, 4, 8]
    # Unscored commits do not count, DONTBUILD pushes still do.
    timeline = make_timeline(12, risks={1: None, 2: None}, dontbuild=(4,))
    assert batches(sim.CommitCount(commits=4), timeline) == [0, 6, 10]
    assert sim.parse_strategy("commit_count:commits=24").params == {"commits": 24}
    assert "commit_count" in sim.FAMILIES and "commit_count" not in sim.RISK_FAMILIES


def test_time_since_batch() -> None:
    timeline = make_timeline(10, step_hours=1.0)
    assert batches(sim.TimeSinceBatch(3), timeline) == [0, 3, 6, 9]
    assert batches(sim.TimeSinceBatch(2.5), timeline) == [0, 3, 6, 9]


def test_risk_threshold_product_linear_and_aging() -> None:
    timeline = make_timeline(10, risks={i: 0.1 for i in range(10)})
    # Three commits at 0.1: 1 - 0.9^3 = 0.271 reaches 0.25, two do not.
    assert batches(sim.Rasb(0.25), timeline) == [0, 3, 6, 9]
    strategy = sim.Rasb(0.25)
    sim.replay(strategy, timeline)
    assert math.isclose(1 - 0.9**3, 0.271, abs_tol=1e-3)
    # Linear aggregation sums the scores: 0.1 + 0.1 reaches 0.2.
    assert batches(sim.RasbLa(0.2), timeline) == [0, 2, 4, 6, 8]
    # Aging adds 0.1 per hour waited: at push 2 the hazards are
    # -ln(0.9) + 0.1 and -ln(0.9), so the probability is 0.267 and the batch
    # is due one push earlier than without aging.
    assert batches(sim.Rapb(0.25, aging_rate=0.1), timeline) == [0, 2, 4, 6, 8]
    assert batches(sim.Rapb(0.25, aging_rate=0.0), timeline) == [0, 3, 6, 9]
    assert batches(sim.RapbLa(0.2, aging_rate=0.1), timeline) == [0, 2, 4, 6, 8]


def test_risk_threshold_skips_ignored_and_needs_scores() -> None:
    # Ignored commits (risk None, reason set) do not count; a DONTBUILD push's
    # commits still accumulate even though no decision is taken on it.
    timeline = make_timeline(6, risks={1: None, 2: 0.3, 3: 0.3}, dontbuild=(2,))
    assert batches(sim.Rasb(0.5), timeline) == [0, 3]
    broken = make_timeline(3)
    broken.pushes[1].commits = (sim.Commit("x" * 40, risk=None, ignored_reason=None),)
    assert [c.node for c in broken.unscored_commits()] == ["x" * 40]
    with pytest.raises(ValueError, match="no risk score"):
        sim.replay(sim.Rasb(0.5), broken)
    with pytest.raises(ValueError, match="product aggregation"):
        sim.Rasb(1.5)


def test_production_rule() -> None:
    # Pushes every 30 minutes: 4 hours pass after 8 pushes, before 20.
    timeline = make_timeline(30, step_hours=0.5, pushlog_start=200, dontbuild=(16,))
    production = sim.Production(expanded_labels=frozenset({L1}))
    schedule = sim.replay(production, timeline)
    # Backstops at 0, 8 and then 16 is DONTBUILD so 17, then 25 (4h after 17).
    assert schedule.batch_indices == [0, 8, 17, 25]
    # Expanded labels run where the pushlog id is a multiple of 10: 210 (index 10),
    # 220 (index 20); 200 is a backstop already.
    assert schedule.partial_runs == {L1: [10, 20]}
    assert schedule.decisions[10] == frozenset({L1})
    assert schedule.jobs_at(0, timeline) == 302 and schedule.jobs_at(10, timeline) == 1
    # Push-count trigger: pushes every 6 minutes reach 20 pushes in 2 hours.
    dense = make_timeline(45, step_hours=0.1, pushlog_start=300)
    assert batches(sim.Production(), dense) == [0, 20, 40]


def test_pushes_aged_families() -> None:
    # Hourly pushes: a push is an hour, so aging by pushes matches aging by
    # hours. Safe commits (hazard 0.0513) plus 0.02 per unit waited reach the
    # 0.5 product threshold (hazard sum 0.693) seven pushes after a batch.
    hourly = make_timeline(30, step_hours=1.0, pushlog_start=0)
    dense = make_timeline(30, step_hours=0.1, pushlog_start=0)
    assert batches(sim.RapbPushes(threshold=0.5, aging_rate=0.02), hourly) == [
        0,
        7,
        14,
        21,
        28,
    ]
    assert batches(sim.Rapb(threshold=0.5, aging_rate=0.02), hourly) == [
        0,
        7,
        14,
        21,
        28,
    ]
    # Pushes every 6 minutes: the pushes-aged family is unchanged, hours
    # aging has ten times less to add and waits twelve pushes.
    assert batches(sim.RapbPushes(threshold=0.5, aging_rate=0.02), dense) == [
        0,
        7,
        14,
        21,
        28,
    ]
    assert batches(sim.Rapb(threshold=0.5, aging_rate=0.02), dense) == [0, 12, 24]
    linear = sim.RapbLaPushes(threshold=0.5, aging_rate=0.02)
    assert batches(linear, dense) == batches(linear, hourly)
    assert batches(linear, hourly) == batches(
        sim.RapbLa(threshold=0.5, aging_rate=0.02), hourly
    )
    assert linear.name == "rapb_la_pushes(aging_rate=0.02,threshold=0.5)"
    for family in ("rapb_pushes", "rapb_la_pushes"):
        assert family in sim.FAMILIES and family in sim.RISK_FAMILIES
    assert "fixed_interval" not in sim.RISK_FAMILIES


def test_latency_in_pushes() -> None:
    assert sim.latency_key("detect") == "detect_hours"
    assert sim.latency_key("detect_pushes") == "detect_pushes"
    assert sim.latency_label("detect") == "detect hours"
    assert sim.latency_label("window_pushes") == "window pushes"
    # Hourly pushes, a batch every 4 pushes, culprits at 5 and 6: both
    # detected at 8, three and two pushes (and hours) later; the window
    # spans 4 to 8 for both.
    timeline = make_timeline(12, regressions=((1, 5, (L1,)), (2, 6, (L1,))))
    schedule = sim.replay(sim.FixedInterval(push_interval=4), timeline)
    detections = sim.detect(schedule, timeline)
    window = sim.Window(timeline.pushes[0].time, timeline.pushes[-1].time)
    result = sim.evaluate(timeline, schedule, detections, window, bootstrap_samples=10)
    assert sim.objectives(result, "jobs", "detect_pushes", "mean")[1] == 2.5
    assert sim.objectives(result, "jobs", "window_pushes", "max")[1] == 4.0
    assert sim.objectives(result, "jobs", "detect", "mean")[1] == 2.5
    low, high = result["detect_pushes"]["mean_ci95"]
    assert 2.0 <= low <= high <= 3.0
    assert result["window_pushes"]["mean_ci95"] == (4.0, 4.0)
    assert sim.percentile([3.0, 1.0, 2.0, 4.0], 0.5) == 3.0
    assert (
        sim.percentile([3.0, 1.0, 2.0], 0.9) == 3.0 and sim.percentile([], 0.9) == 0.0
    )


def _capacity(workers: int, background: dict[int, float] | None = None):
    """L1 and L2 on a small pool at 60 minutes a run; the rest of the suite on a huge one.

    Three suite labels are mapped so the platform fallback sends the other
    297 to the huge pool too, keeping the small pool's queue to L1 and L2.
    """
    suite = sorted(SUITE)[:3]
    return sim.CapacityModel(
        pools={
            "hw/p": sim.PoolCapacity(
                "hw/p", {"2026-05": workers}, listed_workers=workers
            ),
            "hw/suite": sim.PoolCapacity("hw/suite", {"2026-05": 1000}),
        },
        labels={
            L1: sim.LabelRuntime("hw/p", 60.0),
            L2: sim.LabelRuntime("hw/p", 60.0),
            **{label: sim.LabelRuntime("hw/suite", 1.0) for label in suite},
        },
        background={"hw/p": background or {}, "hw/suite": {}},
    )


def _hours(when, since):
    return (when - since).total_seconds() / 3600


def test_capacity_model_round_trip_and_fallbacks() -> None:
    model = _capacity(3, {int(START.timestamp() // 3600): 2.4})
    data = json.loads(json.dumps(model.to_dict()))
    again = sim.CapacityModel.from_dict(data)
    assert again.pools["hw/p"].workers_at(START) == 3
    # Months without data take the nearest earlier month, else the first.
    assert again.pools["hw/p"].workers_at(START + timedelta(days=60)) == 3
    assert again.pools["hw/p"].workers_at(START - timedelta(days=60)) == 3
    # Background work rounds the free workers down to at least one.
    assert again.available("hw/p", START) == 1
    assert again.available("hw/p", START + timedelta(hours=1)) == 3
    # A label of the same platform borrows the pool most of its labels use,
    # and that pool's typical run time.
    other = "test-linux2404-64-shippable/opt-browsertime-tp6-firefox-p299"
    assert other not in model.labels
    assert model.pool_of(other) == "hw/suite"
    assert model.minutes_of(other, "hw/suite") == 1.0
    assert (
        model.pool_of("test-android-hw-a55-14-0-aarch64-shippable/opt-raptor-x") is None
    )


def test_capacity_queries_share_the_perf_task_filter() -> None:
    since, until = date(2026, 9, 1), date(2026, 9, 3)
    predicate = extraction.perf_task_filter()
    assert "tags.project = 'autoland'" in predicate and "--" not in predicate
    assert predicate in extraction.build_task_costs_query(since, until)
    background = extraction.build_capacity_query(
        "background", since, until, pools="'hw/p', 'hw/q'"
    )
    assert f"NOT {predicate}" in background and "IN ('hw/p', 'hw/q')" in background
    assert 'submission_date >= "2026-09-01"' in background
    assert 'submission_date < "2026-09-03"' in background
    builds = extraction.build_capacity_query(
        "builds",
        since,
        until,
        build_labels="'build-linux64-shippable/opt'",
        min_labels="300",
    )
    assert "< 300" in builds and "IN ('build-linux64-shippable/opt')" in builds
    for name in ("labels", "pools", "workers", "eligible"):
        query = extraction.build_capacity_query(name, since, until, pools="'hw/p'")
        assert "{" not in query and "}" not in query


def test_fetch_capacity_builds_the_model_from_the_queries() -> None:
    pool = "releng-hardware/gecko-t-linux-talos-2404"

    class Client:
        queries: list[str] = []

        def execute(self, data_source_id, sql):
            self.queries.append(sql)
            if "AS wait_p50" in sql:
                return [
                    {
                        "pool": pool,
                        "runs": 990,
                        "wait_p50": 5.0,
                        "wait_p90": 30.0,
                        "run_p50": 20.0,
                        "run_p90": 40.0,
                    },
                    {
                        "pool": "hw/rare",
                        "runs": 1,
                        "wait_p50": 1.0,
                        "wait_p90": 1.0,
                        "run_p50": 1.0,
                        "run_p90": 1.0,
                    },
                ]
            if "AS p50_seconds" in sql:
                return [
                    {"label": L1, "pool": pool, "runs": 900, "p50_seconds": 1200},
                    {"label": L1, "pool": "hw/rare", "runs": 1, "p50_seconds": 60},
                    {"label": L2, "pool": pool, "runs": 90, "p50_seconds": None},
                ]
            if "AS workers" in sql:
                return [{"pool": pool, "month": "2026-09", "workers": 12}]
            if "AS busy" in sql:
                return [{"pool": pool, "hour": 3600 * 1000, "busy": 2.5}]
            if "AS eligible_p50" in sql:
                return [{"pool": pool, "eligible_p50": 90.0}]
            assert "AS with_build" in sql
            return [
                {
                    "label": "build-linux64-shippable/opt",
                    "with_build": 30,
                    "pushes": 100,
                }
            ]

    client = Client()
    model = sim.fetch_capacity(
        client,
        63,
        datetime(2026, 9, 1, tzinfo=UTC),
        datetime(2026, 9, 30, tzinfo=UTC),
        list_workers=False,
    )
    # Six queries, every one dated: the rare pool is below the share kept.
    assert len(client.queries) == 6
    assert all('submission_date < "2026-10-01"' in q for q in client.queries)
    assert set(model.pools) == {pool}
    capacity = model.pools[pool]
    assert capacity.workers_by_month == {"2026-09": 12}
    assert capacity.eligible_minutes == 90.0 and capacity.build_share == 0.3
    assert capacity.observed_wait_minutes == {"p50": 5.0, "p90": 30.0}
    assert capacity.runs_per_day == pytest.approx(990 / 29)
    # L2 has no run time and is left to the pool's fallback.
    assert model.labels == {L1: sim.LabelRuntime(pool, 20.0)}
    assert model.background == {pool: {1000: 2.5}}
    assert model.meta["autoland_perf_runs"] == 991 and model.meta["labels"] == 1


def test_simulate_ci_queues_runs_and_backfills() -> None:
    # Hourly pushes, a batch every 4 pushes, two regressions caught by the
    # batch on push 8. L1 and L2 share one worker: the first completes 60
    # minutes after the build, the second 120.
    timeline = make_timeline(12, regressions=((1, 5, (L1,)), (2, 6, (L2,))))
    schedule = sim.replay(sim.FixedInterval(push_interval=4), timeline)
    detections = sim.detect(schedule, timeline)
    settings = sim.CiSettings(
        _capacity(1),
        build_minutes=99.0,
        backfill="all",
        record_waits=True,
    )
    result = sim.simulate_ci(schedule, timeline, detections, settings)
    assert result.jobs == 3 * 302 and result.unknown_labels == 0
    batch = timeline.pushes[8].time + timedelta(minutes=99)
    first, second = sorted((result.detect_time[1], result.detect_time[2]))
    assert first == batch + timedelta(minutes=60)
    assert second == batch + timedelta(minutes=120)
    # Backfills: L1 on pushes 5, 6, 7 and L2 on pushes 5, 6, 7 (push 4 ran).
    assert result.backfill_jobs == {1: 3, 2: 3}
    # Identification needs the culprit and the push before it: for culprit 5
    # that is pushes 4 (ran in the batch) and 5 (backfilled); for culprit 6
    # pushes 5 and 6, both backfilled. Everything queues behind the batch on
    # the single worker, so identification comes after detection.
    for summary_id in (1, 2):
        identified = result.identify_time[summary_id]
        assert identified is not None
        assert identified > result.detect_time[summary_id]
    assert result.waits["hw/p"][:2] == [0.0, 60.0]

    # Two workers: both labels finish 60 minutes after the build and no run waits.
    settings = sim.CiSettings(
        _capacity(2),
        build_minutes=99.0,
        backfill="all",
        record_waits=True,
    )
    result = sim.simulate_ci(schedule, timeline, detections, settings)
    assert (
        result.detect_time[1] == result.detect_time[2] == batch + timedelta(minutes=60)
    )
    assert result.waits["hw/p"] and all(wait == 0.0 for wait in result.waits["hw/p"])

    # Without backfills the culprit of regression 2 is never identified: push
    # 5 has no data for L2. Regression 1's culprit 5 is not identified either.
    settings = sim.CiSettings(_capacity(2), build_minutes=99.0, backfill="none")
    result = sim.simulate_ci(schedule, timeline, detections, settings)
    assert result.backfill_jobs == {}
    assert result.identify_time == {1: None, 2: None}

    # Background work taking the second worker away for the batch's hour
    # serializes the two runs again.
    hour = int((batch).timestamp() // 3600)
    model = _capacity(2, {hour: 1.0, hour + 1: 1.0})
    result = sim.simulate_ci(
        schedule,
        timeline,
        detections,
        sim.CiSettings(model, 99.0, "none"),
    )
    assert sorted((result.detect_time[1], result.detect_time[2]))[
        1
    ] == batch + timedelta(minutes=120)


def test_detection_starts_the_backfills() -> None:
    # 60 hourly pushes, a batch every 4, culprit 5 on L1 caught by the batch
    # on push 8. Two workers: nothing queues. The backfills start when its
    # L1 run completes and identification follows them.
    timeline = make_timeline(60, regressions=((1, 5, (L1,)),))
    schedule = sim.replay(sim.FixedInterval(push_interval=4), timeline)
    detections = sim.detect(schedule, timeline)
    batch = timeline.pushes[8].time + timedelta(minutes=99)
    result = sim.simulate_ci(
        schedule, timeline, detections, sim.CiSettings(_capacity(2), 99.0)
    )
    assert result.detect_time[1] == batch + timedelta(minutes=60)
    identified = result.identify_time[1]
    assert identified is not None and identified > result.detect_time[1]
    assert result.backfill_jobs == {1: 3}

    # Push 5 ran nothing, so its backfill builds first: eligible at the
    # detection plus the 99 minute build, then 60 minutes of run.
    result = sim.simulate_ci(
        schedule, timeline, detections, sim.CiSettings(_capacity(1), 99.0)
    )
    identified = result.identify_time[1]
    assert identified is not None
    assert identified >= result.detect_time[1] + timedelta(minutes=99 + 60)

    window = sim.Window(
        timeline.pushes[0].time, timeline.pushes[-1].time + timedelta(hours=1)
    )
    metrics = sim.evaluate(timeline, schedule, detections, window, 0, result)
    assert metrics["identified"] == 1
    assert metrics["total_jobs"] == metrics["jobs"] + metrics["backfill_jobs"]
    assert sim.objectives(metrics, "total_jobs", "identify", "mean")[
        1
    ] == pytest.approx(metrics["identify_hours"]["mean"])


def test_ci_detection_follows_the_first_label_to_complete() -> None:
    # Culprit 5 regressed both labels. L2 sorts first, so push order detects
    # through it, but its runs take five hours and L1's one; on three
    # workers nothing queues, L1 completes first and the detection and the
    # backfills follow it.
    timeline = make_timeline(60, regressions=((1, 5, (L1, L2)),))
    schedule = sim.replay(sim.FixedInterval(push_interval=4), timeline)
    detections = sim.detect(schedule, timeline)
    assert detections[0].label == L2
    assert [d.label for d in detections[0].alternatives] == [L1]
    assert [d.label for d in detections[0].candidates()] == [L2, L1]
    model = _capacity(3)
    model.labels[L2] = sim.LabelRuntime("hw/p", 300.0)
    batch = timeline.pushes[8].time + timedelta(minutes=99)

    result = sim.simulate_ci(
        schedule, timeline, detections, sim.CiSettings(model, build_minutes=99.0)
    )
    assert result.detect_time[1] == batch + timedelta(minutes=60)
    # Both labels are backfilled on pushes 5 to 7 from that moment.
    assert result.backfill_jobs == {1: 6}


def test_identification_waits_for_the_sheriff() -> None:
    # Every push runs the suite, so the culprit and the push before it have
    # data when the detecting run completes; the sheriff still identifies
    # the culprit only once turned to it, 30 minutes later. A regression
    # naming no label is identified then too.
    timeline = make_timeline(60, regressions=((1, 5, (L1,)), (2, 7, ())))
    schedule = sim.replay(sim.FixedInterval(push_interval=1), timeline)
    detections = sim.detect(schedule, timeline)
    settings = sim.CiSettings(
        _capacity(4), build_minutes=99.0, sheriff_delay_minutes=30.0
    )
    result = sim.simulate_ci(schedule, timeline, detections, settings)
    assert result.detect_time[1] == timeline.pushes[5].time + timedelta(minutes=99 + 60)
    assert result.backfill_jobs == {}
    for summary_id in (1, 2):
        assert result.identify_time[summary_id] == result.detect_time[
            summary_id
        ] + timedelta(minutes=30)


def test_backfill_builds_follow_the_measured_share() -> None:
    timeline = make_timeline(60, regressions=((1, 5, (L1,)),))
    schedule = sim.replay(sim.FixedInterval(push_interval=4), timeline)
    detections = sim.detect(schedule, timeline)

    def identified(share, rule):
        model = _capacity(4)
        model.pools["hw/p"].build_share = share
        settings = sim.CiSettings(model, build_minutes=99.0, backfill_builds=rule)
        when = sim.simulate_ci(schedule, timeline, detections, settings).identify_time[
            1
        ]
        assert when is not None
        return when

    # Backfills on pushes 5, 6, 7, none of which ran anything. With every such
    # push holding a build they run at once; with none, each builds first.
    assert identified(0.0, "measured") == identified(1.0, "measured") + timedelta(
        minutes=99
    )
    assert identified(1.0, "always") == identified(0.0, "measured")
    assert identified(None, "measured") == identified(0.0, "measured")
    # The draw is stable per push and pool.
    assert sim.capacity.build_present(1005, "hw/p", 0.5) == sim.capacity.build_present(
        1005, "hw/p", 0.5
    )
    assert sim.capacity.build_present(1005, "hw/p", 1.0)
    assert not sim.capacity.build_present(1005, "hw/p", 0.0)
    assert sim.capacity.build_label_for_pool("releng-hardware/gecko-t-osx-1500-m4") == (
        "build-macosx64-aarch64-shippable/opt"
    )
    assert sim.capacity.build_label_for_pool("releng-hardware/gecko-t-osx-1400-r8") == (
        "build-macosx64-shippable/opt"
    )
    assert sim.capacity.build_label_for_pool(
        "proj-autophone/gecko-t-lambda-perf-a55"
    ) == ("build-android-aarch64-shippable/opt")
    assert sim.capacity.build_label_for_pool("gecko-t/misc") is None


def test_evaluate_with_ci_result_and_objectives() -> None:
    timeline = make_timeline(12, regressions=((1, 5, (L1,)), (2, 6, (L2,))))
    schedule = sim.replay(sim.FixedInterval(push_interval=4), timeline)
    detections = sim.detect(schedule, timeline)
    settings = sim.CiSettings(_capacity(2), build_minutes=60.0, backfill="all")
    ci = sim.simulate_ci(schedule, timeline, detections, settings)
    window = sim.Window(
        timeline.pushes[0].time, timeline.pushes[-1].time + timedelta(hours=1)
    )
    result = sim.evaluate(timeline, schedule, detections, window, 10, ci)
    # Culprit 5 caught by the batch on push 8: 3 hours to the push, plus the
    # hour of build and the hour of the run.
    assert result["detect_hours"]["mean"] == 2.5
    assert result["detect_ci_hours"]["mean"] == 4.5
    assert result["identify_hours"]["mean"] > result["detect_ci_hours"]["mean"]
    assert result["identified"] == 2
    assert result["identify_hours"]["ids"] == result["detect_ci_hours"]["ids"] == [1, 2]
    assert result["backfill_jobs"] == 6 and result["total_jobs"] == result["jobs"] + 6
    assert "mean_ci95" in result["identify_hours"]
    assert (
        sim.objectives(result, "total_jobs", "identify", "mean")[0]
        == result["total_jobs"]
    )
    assert sim.latency_label("detect_ci") == "detect hours"
    assert sim.latency_label("identify") == "identify hours"
    # A pool with its own measured eligible time ignores the settings' build time.
    model = _capacity(2)
    model.pools["hw/p"].eligible_minutes = 120.0
    again = sim.CapacityModel.from_dict(json.loads(json.dumps(model.to_dict())))
    assert again.pools["hw/p"].eligible_minutes == 120.0
    ci = sim.simulate_ci(
        schedule,
        detections=detections,
        timeline=timeline,
        settings=sim.CiSettings(again, build_minutes=60.0, backfill="none"),
    )
    assert ci.detect_time[1] == timeline.pushes[8].time + timedelta(minutes=120 + 60)
    plain = sim.evaluate(timeline, schedule, detections, window)
    with pytest.raises(ValueError, match="capacity"):
        sim.objectives(plain, "jobs", "detect_ci", "mean")
    with pytest.raises(ValueError, match="capacity"):
        sim.objectives(plain, "total_jobs", "detect", "mean")


def test_recorded_production_uses_the_pushes() -> None:
    timeline = make_timeline(12, full=(0, 8))
    timeline.pushes[4].ran_labels = frozenset({L1, L2})
    timeline.pushes[6].ran_labels = frozenset({L2})
    strategy = sim.RecordedProduction(expanded_labels=frozenset({L1}))
    schedule = sim.replay(strategy, timeline)
    assert schedule.batch_indices == [0, 8]
    # Only expanded labels count on other pushes; backfills such as L2 do not.
    assert schedule.partial_runs == {L1: [4]}
    assert schedule.decisions[6] is None
    assert "production_recorded" in sim.FAMILIES


def test_detect_and_metrics() -> None:
    timeline = make_timeline(
        20,
        pushlog_start=0,
        regressions=[
            (1, 5, [L1]),  # caught at 8, previous run at 4
            (2, 6, [L2]),  # caught at 8 too
            (3, 7, [L1]),  # caught at 8: merged with 1 on the same label
            (4, 12, []),  # no label: any batch, caught at 12 itself
            (5, 19, [L1]),  # nothing after it
        ],
    )
    schedule = sim.replay(sim.FixedInterval(4), timeline)
    assert schedule.batch_indices == [0, 4, 8, 12, 16]
    detections = sim.detect(schedule, timeline)
    first = detections[0]
    assert (first.detected_index, first.previous_index) == (8, 4)
    assert first.detect_hours(timeline) == 3
    assert first.detect_pushes == 3 and first.window_pushes == 4
    assert detections[3].detected_index == 12 and detections[3].label is None
    assert detections[4].detected_index is None and detections[4].window_pushes is None

    window = sim.Window(timeline.pushes[4].time, timeline.pushes[18].time)
    result = sim.evaluate(timeline, schedule, detections, window, bootstrap_samples=50)
    # Pushes 4 to 17 are in the window: batches at 4, 8, 12 and 16.
    assert result["batches"] == 4 and result["jobs"] == 4 * 302
    assert (
        result["pushes_per_batch"]["mean"] == 4
        and result["hours_between_batches"]["max"] == 4
    )
    assert result["regressions"] == 4 and result["detected"] == 4
    assert result["detected_ids"] == [1, 2, 3, 4] and result["missed_ids"] == []
    assert result["detect_hours"]["mean"] == pytest.approx((3 + 2 + 1 + 0) / 4)
    assert result["merged"] == 1
    assert result["detect_hours"]["mean_ci95"] is not None

    # The last regression is in the window but never caught.
    whole = sim.Window(timeline.start, timeline.end + timedelta(seconds=1))
    result = sim.evaluate(timeline, schedule, detections, whole)
    assert result["missed_ids"] == [5]
    assert sim.objectives(result, "jobs", "detect") == (5 * 302, pytest.approx(1.5))

    # A strategy that misses something production caught is infeasible.
    schedule, detections = _replay_and_detect(sim.Production(), timeline)
    baseline = sim.evaluate(timeline, schedule, detections, whole)
    assert sim.is_feasible(result, baseline)
    schedule, detections = _replay_and_detect(sim.FixedInterval(50), timeline)
    sparse = sim.evaluate(timeline, schedule, detections, whole)
    assert not sim.is_feasible(sparse, baseline)

    # Feasibility follows the optimized latency: a detected regression may
    # have no CI completion or identification, and a schedule lacking those
    # for more regressions than production would be judged on fewer.
    covered = {"detect_hours": {"ids": [1, 2]}, "identify_hours": {"ids": [1, 2]}}
    partial = {"detect_hours": {"ids": [1, 2]}, "identify_hours": {"ids": [1]}}
    assert sim.is_feasible(partial, covered)
    assert not sim.is_feasible(partial, covered, "identify")
    assert sim.missed_ids(partial, covered, "identify") == [2]
    with pytest.raises(ValueError, match="capacity"):
        sim.is_feasible(sparse, covered, "identify")


def test_batches_run_only_the_labels_of_their_suite() -> None:
    # L2 leaves the suite at the full-suite push 12, so the batch on 16 runs
    # L1 but not L2: it catches the L1 regression of push 13 and not the L2
    # one, which nothing runs afterwards.
    timeline = make_timeline(
        20, full=(0, 12), regressions=[(1, 13, [L1]), (2, 13, [L2])]
    )
    timeline.pushes[12].ran_labels = SUITE | {L1}
    schedule = sim.replay(sim.FixedInterval(4), timeline)
    assert schedule.batch_indices == [0, 4, 8, 12, 16]
    assert schedule.runs_of(L1) == [0, 4, 8, 12, 16]
    assert schedule.runs_of(L2) == [0, 4, 8]
    assert schedule.runs_of(None) == schedule.batch_indices
    detections = sim.detect(schedule, timeline)
    assert detections[0].detected_index == 16
    assert detections[1].detected_index is None
    # Partial runs of a label count whatever the suite holds, and a batch
    # whose suite is unknown counts for every label.
    suite = frozenset({L1})
    by_hand = sim.Schedule([None] * 10, [0, 8], {L2: [4]}, [None, suite])
    assert by_hand.runs_of(L1) == [0, 8] and by_hand.runs_of(L2) == [0, 4]

    # Before the first full-suite push the suite is only borrowed from it, so
    # the batches there are credited with the alerted L2 as well, in the CI
    # replay too; from push 12 on the recorded suite rules.
    timeline = make_timeline(
        20, full=(12,), regressions=[(1, 1, [L2]), (2, 13, [L2]), (3, 13, [L1])]
    )
    timeline.pushes[12].ran_labels = SUITE | {L1}
    assert timeline.known_suite_at(4) is None and timeline.suite_at(4) == SUITE | {L1}
    assert timeline.known_suite_at(13) == SUITE | {L1}
    schedule = sim.replay(sim.FixedInterval(4), timeline)
    assert schedule.runs_of(L2) == [0, 4, 8]
    detections = sim.detect(schedule, timeline)
    assert [d.detected_index for d in detections] == [4, None, 16]
    settings = sim.CiSettings(_capacity(2), build_minutes=99.0, backfill="none")
    result = sim.simulate_ci(schedule, timeline, detections, settings)
    assert result.detect_time[1] == timeline.pushes[4].time + timedelta(minutes=99 + 60)
    assert 2 not in result.detect_time


def test_overlapping_detections_share_their_backfills() -> None:
    # Culprits 5 and 6 on L1, both caught by the batch on push 8 at once: the
    # first detection backfills L1 on pushes 5 to 7, the second finds them
    # requested and still gets its culprit identified from them.
    timeline = make_timeline(12, regressions=((1, 5, (L1,)), (2, 6, (L1,))))
    schedule = sim.replay(sim.FixedInterval(push_interval=4), timeline)
    detections = sim.detect(schedule, timeline)
    settings = sim.CiSettings(_capacity(4), build_minutes=99.0)
    result = sim.simulate_ci(schedule, timeline, detections, settings)
    assert result.backfill_jobs == {1: 3}
    assert result.identify_time[1] is not None and result.identify_time[2] is not None
    assert result.identify_time[2] >= result.detect_time[2]


def _replay_and_detect(strategy, timeline):
    schedule = sim.replay(strategy, timeline)
    return schedule, sim.detect(schedule, timeline)


def test_windows_and_default_bounds() -> None:
    timeline = make_timeline(100, regressions=[(1, 60, [L1]), (2, 80, [L1])])
    since, until = sim.default_bounds(timeline)
    assert since == timeline.pushes[60].time
    assert until == timeline.pushes[80].time + timedelta(seconds=1)
    optimize, evaluate = sim.make_windows(
        timeline, timeline.pushes[70].time, warmup_pushes=50
    )
    assert (optimize.start, optimize.end) == (
        timeline.pushes[60].time,
        timeline.pushes[70].time,
    )
    assert (evaluate.start, evaluate.end) == (timeline.pushes[70].time, until)
    # The warm-up pushes the start forward when the bounds begin earlier.
    optimize, _ = sim.make_windows(
        timeline, timeline.pushes[70].time, since=timeline.start, warmup_pushes=10
    )
    assert optimize.start == timeline.pushes[10].time
    with pytest.raises(ValueError, match="must lie inside"):
        sim.make_windows(timeline, timeline.pushes[90].time)


def test_parse_strategy_and_presets() -> None:
    assert sim.parse_strategy("fixed_interval:push_interval=64").params == {
        "push_interval": 64
    }
    rapb = sim.parse_strategy("rapb:threshold=0.77,aging_rate=0.018")
    assert rapb.params == {"threshold": 0.77, "aging_rate": 0.018}
    assert rapb.name == "rapb(aging_rate=0.018,threshold=0.77)"
    assert sim.parse_strategy("preset:perf_time_batch").params == {"hours": 3.0}
    assert sim.parse_strategy("production").name == "production"
    with pytest.raises(ValueError, match="Unknown strategy family"):
        sim.parse_strategy("nope:x=1")
    with pytest.raises(ValueError, match="Unknown preset"):
        sim.parse_strategy("preset:nope")
    assert set(sim.PRESETS) == {
        "perf_fixed_batch",
        "perf_time_batch",
        "perf_rasb",
        "perf_rasb_la",
        "perf_rapb",
        "perf_rapb_la",
    }


def _fixture():
    with open(FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _fixture_timeline(rows):
    pushes = [
        sim.Push(
            index=i,
            push_id=i,
            pushlog_id=row["pushlog_id"],
            time=datetime.fromtimestamp(row["date"], UTC),
            commits=tuple(
                sim.Commit(
                    c["node"], risk=c["risk_score"], ignored_reason=c["ignored_reason"]
                )
                for c in row["commits"]
            ),
            dontbuild=bool(row["dontbuild"]),
        )
        for i, row in enumerate(rows)
    ]
    return sim.Timeline(pushes, [])


@pytest.mark.parametrize(
    "expected_key, family",
    [
        ("perf_fixed_batch_20", "fixed_interval"),
        ("perf_fixed_batch (64)", "fixed_interval"),
        ("perf_time_batch", "time_since_batch"),
        ("perf_rasb", "rasb"),
        ("perf_rasb_la", "rasb_la"),
        ("perf_rapb", "rapb"),
        ("perf_rapb_la", "rapb_la"),
    ],
)
def test_replay_matches_the_in_tree_strategies(expected_key, family) -> None:
    """The decisions match a replay of the real gecko_taskgraph classes.

    The fixture was produced by running the in-tree strategies, with the
    DONTBUILD replay fix, over 420 autoland pushes with bugbug's recorded
    predictions, seeding the first push as a batch. It records the parameters
    each strategy ran with, the in-tree values of the time, which pin the
    arithmetic whatever the presets are today.
    """
    fixture = _fixture()
    timeline = _fixture_timeline(fixture["pushes"])
    recorded = fixture["parameters"][expected_key]
    params = {
        key: recorded[key]
        for key in sim.FAMILIES[family].search_space
        if key in recorded
    }
    strategy = sim.make_strategy(family, params)
    indices = sim.replay(strategy, timeline).batch_indices
    ours = [timeline.pushes[i].pushlog_id for i in indices]
    first = timeline.pushes[0].pushlog_id
    # Every strategy starts with a cold-start batch, the replay's seed.
    assert ours[0] == first
    # The recorded replay never lists the first push, seed or not.
    assert [p for p in ours if p != first] == fixture["expected_batches"][expected_key]


def test_production_replay_matches_the_recorded_backstops() -> None:
    """The rule reproduces production's recorded backstops.

    Decision tasks do not always finish in push order: when a later push's
    task indexes itself as the latest backstop first, the earlier push sees a
    negative distance and is not a backstop, which shifts the chain by one
    push until a time-triggered backstop resynchronizes it. So from the first
    recorded backstop the two chains agree up to such one-push shifts, and
    from the first time-triggered backstop they agree exactly.
    """
    fixture = _fixture()
    rows = fixture["pushes"]
    recorded = [row["pushlog_id"] for row in rows if row["backstop"]]
    by_id = {row["pushlog_id"]: row for row in rows}

    def replay_from(pushlog_id):
        start = next(i for i, row in enumerate(rows) if row["pushlog_id"] == pushlog_id)
        timeline = _fixture_timeline(rows[start:])
        schedule = sim.replay(sim.Production(), timeline)
        return [timeline.pushes[i].pushlog_id for i in schedule.batch_indices]

    ours = replay_from(recorded[0])
    assert len(ours) == len(recorded)
    shifted = sorted(set(ours) ^ set(recorded))
    assert len(shifted) <= 6
    assert all(b - a == 1 for a, b in zip(shifted[::2], shifted[1::2]))
    assert all(a in ours and b in recorded for a, b in zip(shifted[::2], shifted[1::2]))

    time_triggered = next(
        b
        for a, b in zip(recorded, recorded[1:])
        if by_id[b]["date"] - by_id[a]["date"] >= 4 * 3600
    )
    assert replay_from(time_triggered) == [p for p in recorded if p >= time_triggered]


def test_timeline_from_records() -> None:
    def push(push_id, time, nodes, labels=(), pushlog_id=None, desc="Bug 1 - x"):
        return {
            "push_id": push_id,
            "pushlog_id": pushlog_id,
            "time": time,
            "commits": [
                {"revision": n, "desc": desc, "bug_id": 1, "backout": False}
                for n in nodes
            ],
            "perf_jobs": [{"label": label} for label in labels],
        }

    full = [
        f"test-linux1804-64-shippable/opt-browsertime-tp6-firefox-p{k}"
        for k in range(300)
    ]
    records = [
        push(
            2,
            "2026-05-01T01:00:00Z",
            ["b" * 40],
            [L1, "test-linux2404-64/opt-browsertime-tp6-chrome-amazon"],
            11,
        ),
        push(
            1, "2026-05-01T00:00:00Z", ["a" * 40], full, 10, desc="Bug 1 - x DONTBUILD"
        ),
        push(3, "2026-05-01T02:00:00Z", ["c" * 40], [], 12),
    ]
    regressions = [
        {
            "summary_id": 7,
            "outcome": "concluded",
            "push_id": 2,
            "runnables": [{"label": L1}, {"label": None}],
        },
        {
            "summary_id": 8,
            "outcome": "rejected",
            "push_id": 2,
            "runnables": [{"label": L1}],
        },
        {"summary_id": 9, "outcome": "attributed", "push_id": 99, "runnables": []},
    ]
    risks = {"a" * 40: (0.2, None), "b" * 40: (None, "merge")}
    timeline = sim.Timeline.from_records(records, regressions, risks)
    assert [p.push_id for p in timeline.pushes] == [1, 2, 3]
    assert timeline.pushes[0].dontbuild and not timeline.pushes[1].dontbuild
    assert timeline.pushes[0].full_suite and len(timeline.suite_at(2)) == 300
    # Retired platforms are folded and non-Firefox applications dropped.
    assert timeline.pushes[1].ran_labels == frozenset({L1})
    assert "linux2404" in next(iter(timeline.pushes[0].ran_labels))
    assert [r.summary_id for r in timeline.regressions] == [7]
    assert timeline.regressions[0].labels == frozenset({L1})
    assert timeline.pushes[0].commits[0].risk == 0.2
    assert timeline.pushes[1].commits[0].ignored_reason == "merge"
    assert timeline.pushes[2].commits[0].risk is None and timeline.has_pushlog_ids
    assert [c.node for c in timeline.unscored_commits()] == ["c" * 40]


def test_expanded_labels_are_derived_from_history() -> None:
    timeline = make_timeline(40, pushlog_start=100, full=(0,))
    for push in timeline.pushes:
        if push.index and push.pushlog_id % 10 == 0:
            push.ran_labels = frozenset({L1})
    timeline.pushes[15].ran_labels = frozenset({L2})
    assert timeline.expanded_labels() == frozenset({L1})
    # Pushes before the first full-suite push have no jobs left, Treeherder
    # having dropped them, so they are not evidence that nothing ran there.
    expired = make_timeline(60, pushlog_start=100, full=(40,))
    expired.pushes[50].ran_labels = frozenset({L1})
    assert expired.expanded_labels() == frozenset({L1})
    assert make_timeline(20, pushlog_start=100, full=()).expanded_labels() == (
        frozenset()
    )
    no_ids = make_timeline(5)
    for push in no_ids.pushes:
        push.pushlog_id = None
    assert no_ids.expanded_labels() == frozenset()
    assert not no_ids.has_pushlog_ids


def test_with_calibration() -> None:
    timeline = make_timeline(5, risks={1: 0.8, 2: None})
    calibrated = timeline.with_calibration(Calibration("v1", (0.0, 1.0), (0.0, 0.5)))
    risks = [commit.risk for push in calibrated.pushes for commit in push.commits]
    assert risks[0] == pytest.approx(0.025) and risks[1] == pytest.approx(0.4)
    # The ignored commit stays unscored and the original timeline is untouched.
    assert (
        risks[2] is None and calibrated.pushes[2].commits[0].ignored_reason == "merge"
    )
    assert timeline.pushes[1].commits[0].risk == 0.8
    assert calibrated.regressions == timeline.regressions
    assert [p.pushlog_id for p in calibrated.pushes] == [
        p.pushlog_id for p in timeline.pushes
    ]


def test_calibrate_timeline_fits_on_the_history_before_the_window(
    monkeypatch,
) -> None:
    from scripts import perf_scheduling_simulator as cli

    # Ten score levels; before the window the three highest are all culprits.
    timeline = make_timeline(
        300, risks={i: 0.1 + 0.8 * (i % 10) / 9 for i in range(300)}
    )
    nodes = [push.commits[0].node for push in timeline.pushes]
    culprits = {nodes[i] for i in range(200) if i % 10 >= 7}
    regressions = [
        {
            "summary_id": i,
            "outcome": "concluded",
            "push_id": push.push_id,
            "push_time": push.time.isoformat(),
            "culprit_commits": [nodes[i]],
        }
        for i, push in enumerate(timeline.pushes)
        if nodes[i] in culprits
    ]
    statuses = [
        {
            "push_id": push.push_id,
            "push_time": push.time.isoformat(),
            "label_state": "culprit" if nodes[i] in culprits else "clean",
            "settled": True,
            "commits": [
                {
                    "node": nodes[i],
                    "bug_id": 1,
                    "backout": False,
                    "landing_id": "x",
                    "landing_size": 1,
                }
            ],
        }
        for i, push in enumerate(timeline.pushes)
    ]
    monkeypatch.setattr(cli.datasets, "ensure_dataset", lambda path: None)
    monkeypatch.setattr(cli.datasets, "get_perf_regressions", lambda: iter(regressions))
    monkeypatch.setattr(cli.push_status, "read_push_status", lambda: list(statuses))
    risks: dict[str, tuple[float | None, str | None]] = {
        node: (timeline.pushes[i].commits[0].risk, None) for i, node in enumerate(nodes)
    }
    window = sim.Window(timeline.pushes[200].time, timeline.end + timedelta(seconds=1))

    calibrated, summary = cli.calibrate_timeline(
        timeline, "fit", risks, "v1", window, fit_on="before"
    )
    assert summary is not None
    assert summary["mode"] == "fit" and summary["fitted_on"] == "before"
    assert summary["commits"] == 200 and summary["positives"] == 60
    # Inside the window the top score level maps to the culprit rate seen
    # before it and the bottom one to zero; the raw timeline is untouched.
    assert calibrated.pushes[209].commits[0].risk == pytest.approx(1.0)
    assert calibrated.pushes[200].commits[0].risk == pytest.approx(0.0)
    assert timeline.pushes[209].commits[0].risk == pytest.approx(0.9)

    assert cli.calibrate_timeline(timeline, "none", risks, "v1", window) == (
        timeline,
        None,
    )
    # The window itself holds no culprits, so fitting within it is refused.
    with pytest.raises(SystemExit, match="0 scored culprits between"):
        cli.calibrate_timeline(timeline, "fit", risks, "v1", window, fit_on="within")


def test_load_timeline_needs_scores_only_before_the_horizon(monkeypatch) -> None:
    from scripts import perf_scheduling_simulator as cli

    # Thirty hourly pushes, the one regression's culprit at push 10, and the
    # scorer has reached the first 25 pushes.
    times = [START + timedelta(hours=i) for i in range(30)]
    pushes = [
        {
            "push_id": 1000 + i,
            "pushlog_id": 100 + i,
            "time": times[i].isoformat(),
            "commits": [{"revision": f"{i:040x}", "desc": "Bug 1 - x"}],
            "perf_jobs": [],
        }
        for i in range(30)
    ]
    regressions = [
        {
            "summary_id": 1,
            "outcome": "concluded",
            "push_id": 1010,
            "runnables": [{"label": L1}],
        }
    ]
    risks = [
        {
            "node": f"{i:040x}",
            "risk_score": 0.1,
            "ignored_reason": None,
            "model_version": "v1",
        }
        for i in range(25)
    ]
    monkeypatch.setattr(cli.datasets, "ensure_dataset", lambda path: None)
    monkeypatch.setattr(cli.datasets, "get_perf_pushes", lambda: iter(pushes))
    monkeypatch.setattr(cli.datasets, "get_perf_regressions", lambda: iter(regressions))
    monkeypatch.setattr(cli.datasets, "get_perf_commit_risk", lambda: iter(risks))

    timeline, _, _ = cli.load_timeline(False, None, None)
    assert len(timeline.pushes) == 30
    # With scores the history ends at the first unscored push, which may
    # follow the horizon: by default just after the last culprit push.
    timeline, scores, version = cli.load_timeline(True, None, None)
    assert len(timeline.pushes) == 25 and len(timeline.regressions) == 1
    assert version == "v1" and scores is not None and len(scores) == 25
    timeline, _, _ = cli.load_timeline(True, None, None, until=times[20])
    assert len(timeline.pushes) == 25
    # A horizon the scores do not reach is refused.
    with pytest.raises(SystemExit, match="3 commits of pushes before"):
        cli.load_timeline(True, None, None, until=times[28])


def test_sweep_and_search_space_for() -> None:
    assert sim.FixedInterval.sweep({"push_interval": (2, 5, True)}, 400) == {
        "push_interval": [2, 3, 4, 5]
    }
    hours = sim.TimeSinceBatch.sweep({"hours": (0.25, 4.0, True)}, 5)
    assert hours is not None
    assert hours["hours"] == pytest.approx([0.25, 0.5, 1.0, 2.0, 4.0])
    thresholds = sim.Rasb.sweep({"threshold": (0.1, 0.5, False)}, 5)
    assert thresholds is not None
    assert thresholds["threshold"] == pytest.approx([0.1, 0.2, 0.3, 0.4, 0.5])
    assert sim.Rapb.sweep(sim.Rapb.search_space, 5) is None

    # Product thresholds keep their bounds; a linear threshold is bounded by
    # the scores: one commit's worth up to 48 hours of pushes.
    timeline = make_timeline(
        200, step_hours=1.0, risks={i: 0.02 * (i % 5 + 1) for i in range(200)}
    )
    assert sim.Rapb.search_space_for(timeline.pushes) == sim.Rapb.search_space
    space = sim.RapbLa.search_space_for(timeline.pushes)
    low, high, log = space["threshold"]
    assert (
        log
        and low == pytest.approx(0.02)
        and high == pytest.approx(49 * 0.06, rel=0.05)
    )
    assert space["aging_rate"] == sim.RapbLa.search_space["aging_rate"]
    # Without scores the class bounds stand.
    assert sim.RasbLa.search_space_for([]) == sim.RasbLa.search_space


def test_optimize_two_parameter_family_evolves() -> None:
    pytest.importorskip("optuna")
    timeline = make_timeline(
        300,
        step_hours=0.5,
        pushlog_start=0,
        risks={i: 0.01 + 0.1 * (i % 7 == 0) for i in range(300)},
        regressions=[(k, 20 * k + 1, [L1]) for k in range(1, 14)],
    )
    # The split must fall between the first and the last regression.
    optimize_window, _ = sim.make_windows(
        timeline, timeline.pushes[240].time, warmup_pushes=10
    )
    baseline = sim.run(sim.Production(), timeline, [optimize_window])[0]
    study = sim.optimize_family(
        "rapb_la", timeline, optimize_window, baseline, n_trials=25, seed=1
    )
    assert len(study.trials) == 25
    low, high, log = study.user_attrs["search_space"]["threshold"]
    assert log and 0 < low < high
    assert all(low <= t.params["threshold"] <= high for t in study.trials)
    assert sim.pareto_points(study)


def test_load_risks() -> None:
    records: list[dict[str, Any]] = [
        {"node": "a", "risk_score": 0.1, "ignored_reason": None, "model_version": "v1"},
        {
            "node": "b",
            "risk_score": None,
            "ignored_reason": "merge",
            "model_version": "v1",
        },
        {"node": "a", "risk_score": 0.9, "ignored_reason": None, "model_version": "v2"},
    ]
    risks, version = sim.load_risks(records, "v1")
    assert risks == {"a": (0.1, None), "b": (None, "merge")} and version == "v1"
    with pytest.raises(ValueError, match="several model versions"):
        sim.load_risks(records)


def test_optimize_family_and_report() -> None:
    pytest.importorskip("optuna")
    # Regressions every 20 pushes up to push 383; the optimize window ends at
    # push 370, so it holds the ones up to 363 and the eval window the last.
    timeline = make_timeline(
        400,
        step_hours=0.5,
        pushlog_start=0,
        regressions=[(k, 20 * k + 3, [L1]) for k in range(1, 20)],
    )
    optimize_window, eval_window = sim.make_windows(
        timeline, timeline.pushes[370].time, warmup_pushes=10
    )
    baseline_optimize, baseline_eval = sim.run(
        sim.Production(), timeline, [optimize_window, eval_window]
    )
    study = sim.optimize_family(
        "fixed_interval",
        timeline,
        optimize_window,
        baseline_optimize,
        n_trials=5,
        latency="detect",
    )
    # An integer parameter is swept over every value, whatever the trial count.
    assert len(study.trials) == 199
    assert study.user_attrs["search_space"] == {"push_interval": [2, 200, True]}
    # Intervals too long for a batch to follow the last regressions before the
    # pushes end are pruned, and say which regressions they missed.
    pruned = sim.pruned_trials(study)
    # An interval is pruned when its last batch before the pushes end (push
    # 399) comes before the last regression of the optimize window, at 363.
    expected = {n for n in range(2, 201) if (399 // n) * n < 363}
    assert expected and {p["params"]["push_interval"] for p in pruned} == expected
    assert all(18 in p["missed_ids"] for p in pruned)
    points = sim.pareto_points(study)
    assert points and all(
        p["metrics"]["detected"] == p["metrics"]["regressions"] for p in points
    )
    assert [p["objectives"][0] for p in points] == sorted(
        p["objectives"][0] for p in points
    )
    rows = sim.evaluate_points(
        "fixed_interval", points, timeline, eval_window, latency="detect"
    )
    assert rows[0]["family"] == "fixed_interval" and "metrics" in rows[0]["eval"]
    matching = sim.matching_points(
        rows, sim.objectives(baseline_optimize, latency="detect")
    )
    assert set(matching) == {"at_cost", "at_latency"}
    # Any summary of the latency can be the objective.
    assert sim.objectives(baseline_optimize, statistic="max") == (
        baseline_optimize["jobs"],
        baseline_optimize["detect_hours"]["max"],
    )
    worst = sim.optimize_family(
        "fixed_interval",
        timeline,
        optimize_window,
        baseline_optimize,
        n_trials=5,
        statistic="max",
    )
    assert all(
        t.values[1] == t.user_attrs["metrics"]["detect_hours"]["max"]
        for t in worst.trials
        if t.values
    )
    assert {c.family for c in strategies.FAMILIES.values()} >= {"rasb", "rapb_la"}
