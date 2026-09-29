# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
from datetime import datetime, timezone

import pytest
import responses

from bugbug import db, perf
from bugbug.perf import extraction, regression_commits

RS = perf.RECORD_SEP
US = perf.UNIT_SEP
SETTLED = datetime(2030, 1, 1, tzinfo=timezone.utc)


def test_split_and_normalize_label() -> None:
    label = "test-linux1804-64-shippable-qr/opt-browsertime-tp6-firefox-amazon"
    assert perf.split_task_label(label) == (
        "linux1804-64-shippable-qr",
        "opt",
        "browsertime-tp6-firefox-amazon",
    )
    assert (
        perf.normalize_task_label(label)
        == "test-linux2404-64-shippable/opt-browsertime-tp6-firefox-amazon"
    )
    assert perf.normalize_task_label("perftest-linux-domcount") == (
        "perftest-linux-domcount"
    )
    assert perf.normalize_platform("windows11-64-24h2-shippable") == (
        "windows11-64-24h2-shippable"
    )


def test_runnable_identity() -> None:
    identity = perf.get_runnable_identity(
        "test-android-hw-a55-14-0-aarch64-shippable/opt-browsertime-tp6m-fenix-amazon"
    )
    assert identity["test_name"] == "browsertime-tp6m-fenix-amazon"
    assert identity["platform_family"] == "android"
    assert identity["platform"] == "android-hw-a55-14-0-aarch64-shippable/opt"
    assert perf.is_perf_task_label("test-linux2404-64-shippable/opt-talos-g1")
    assert not perf.is_perf_task_label("test-linux2404-64/opt-mochitest-plain-1")


def test_merge_alerts_keeps_history_and_labels() -> None:
    previous = [
        _alert(alert_id=1, summary_created="2025-01-01T00:00:00"),
        _alert(alert_id=2, summary_created="2025-02-01T00:00:00", summary_status=5),
        _alert(alert_id=3, summary_created="2025-03-01T00:00:00", task_labels=None),
    ]
    fetched = [
        # Refreshed by the source, but the job behind it is gone.
        _alert(
            alert_id=2,
            summary_created="2025-02-01T00:00:00",
            summary_status=7,
            task_labels=None,
            task_ids=None,
        ),
        # Still unmapped on both sides.
        _alert(alert_id=3, summary_created="2025-03-01T00:00:00", task_labels=None),
        _alert(alert_id=4, summary_created="2025-04-01T00:00:00"),
    ]
    merged = perf.merge_alerts(previous, fetched)
    assert [a["alert_id"] for a in merged] == [1, 2, 3, 4]
    refreshed = merged[1]
    assert refreshed["summary_status_name"] == "fixed"
    assert refreshed["task_labels"] == previous[1]["task_labels"]
    assert refreshed["task_ids"] == previous[1]["task_ids"]
    assert refreshed["normalized_labels"] == previous[1]["normalized_labels"]
    assert merged[2]["task_labels"] == []


def test_parse_push_row() -> None:
    row = {
        "push_id": 10,
        "repository": "autoland",
        "revision": "a" * 40,
        "push_time": "2026-08-01T10:00:00",
        "author": "someone@mozilla.com",
        "commits": f"{'a' * 40}{US}Bug 123 - Fix things r=me{RS}{'b' * 40}{US}Backed out changeset xyz",
        "perf_jobs": (
            f"test-linux2404-64-shippable/opt-talos-g1{US}success{US}600{US}1{US}T1{US}0{RS}"
            f"test-linux2404-64-shippable/opt-talos-g1{US}retry{US}{US}1{US}T1{US}1{RS}"
            f"test-linux2404-64-shippable/opt-browsertime-tp6-firefox-amazon{US}success{US}1200{US}2{US}T2{US}0"
        ),
    }
    push = perf.parse_push_row(row)
    assert push["commits"][0] == {
        "revision": "a" * 40,
        "desc": "Bug 123 - Fix things r=me",
        "bug_id": 123,
        "backout": False,
    }
    assert push["commits"][1]["backout"] is True
    assert push["commits"][1]["bug_id"] is None
    jobs = {job["label"]: job for job in push["perf_jobs"]}
    g1 = jobs["test-linux2404-64-shippable/opt-talos-g1"]
    assert g1["duration"] == 600
    assert g1["runs"] == [
        {"task_id": "T1", "retry_id": 0, "result": "success", "duration": 600},
        {"task_id": "T1", "retry_id": 1, "result": "retry", "duration": None},
    ]
    assert (
        jobs["test-linux2404-64-shippable/opt-browsertime-tp6-firefox-amazon"]["tier"]
        == 2
    )


def test_costs_query_and_normalization() -> None:
    query = perf.build_task_costs_query(
        ("autoland",), datetime(2026, 9, 1).date(), datetime(2026, 9, 3).date()
    )
    assert 'submission_date >= "2026-09-01"' in query
    assert 'submission_date < "2026-09-03"' in query
    assert "tags.project IN ('autoland')" in query

    cost = perf.normalize_cost_row(
        {
            "task_id": "T1",
            "label": "test-linux1804-64-shippable/opt-talos-g1",
            "project": "autoland",
            "task_queue_id": "releng-hardware/gecko-t-linux-talos-2404",
            "runs": 1,
            "duration": 400,
            "cost": None,
            "costed_runs": None,
            "unexpected": "dropped",
        }
    )
    assert set(cost) == set(perf.COST_FIELDS)
    assert cost["task_queue_id"] == "releng-hardware/gecko-t-linux-talos-2404"
    assert cost["submission_date"] is None
    identity = perf.get_runnable_identity(cost["label"])
    assert identity["test_name"] == "talos-g1"
    assert identity["platform_family"] == "linux"


def test_load_task_costs_filters_by_task_id() -> None:
    db.write(
        perf.PERF_TASK_COSTS_DB,
        [
            {
                "task_id": "T1",
                "task_queue_id": "pool-a",
                "cost": None,
                "costed_runs": None,
            },
            {"task_id": "T2", "task_queue_id": "pool-b", "cost": 0.5, "costed_runs": 1},
        ],
    )
    assert perf.load_task_costs({"T2"}) == {"T2": perf.TaskCost("pool-b", 0.5, 1)}
    assert set(perf.load_task_costs()) == {"T1", "T2"}
    pushes = [{"perf_jobs": [{"runs": [{"task_id": "T1"}, {"task_id": None}]}]}]
    assert perf.collect_push_task_ids(pushes) == {"T1"}


def test_label_prices_and_price_labels() -> None:
    amazon = "test-linux2404-64-shippable/opt-browsertime-tp6-firefox-amazon"
    old_amazon = "test-linux1804-64-shippable-qr/opt-browsertime-tp6-firefox-amazon"
    win_g1 = "test-windows11-64-24h2-shippable/opt-talos-g1"
    rows: list[dict] = [
        {"label": amazon, "task_queue_id": "linux-hw", "runs": 1, "duration": 100},
        {"label": old_amazon, "task_queue_id": "linux-hw", "runs": 2, "duration": 600},
        {"label": amazon, "task_queue_id": "linux-cloud", "runs": 1, "duration": 500},
        {"label": win_g1, "task_queue_id": "win-hw", "runs": 1, "duration": 40},
        {"label": win_g1, "task_queue_id": "win-hw", "runs": 0, "duration": 0},
        {"label": None, "task_queue_id": "win-hw", "runs": 1, "duration": 1},
    ]
    prices = perf.build_label_prices(rows)
    # Renamed platforms merge; the median is over runs, the pool the most common.
    assert prices == {
        amazon: perf.LabelPrice("linux-hw", 300, 4),
        win_g1: perf.LabelPrice("win-hw", 40, 1),
    }

    ebay = "test-linux2404-64-shippable/opt-browsertime-tp6-firefox-ebay"
    mac_g1 = "test-macosx1470-64-shippable/opt-talos-g1"
    summary = perf.estimate_labels_cost([old_amazon, win_g1, ebay, mac_g1], prices)
    # ebay borrows the tp6/linux price; mac talos has no sibling on macosx.
    assert summary == {
        "labels": 4,
        "priced": 2,
        "estimated": 1,
        "unpriced": 1,
        "machine_seconds_by_pool": {"linux-hw": 600, "win-hw": 40},
    }


def test_push_cost_summary() -> None:
    push = {
        "perf_jobs": [
            {
                "label": "a",
                "runs": [
                    {
                        "task_id": "T1",
                        "retry_id": 0,
                        "result": "success",
                        "duration": 100,
                    },
                    {
                        "task_id": "T2",
                        "retry_id": 0,
                        "result": "success",
                        "duration": 50,
                    },
                    {
                        "task_id": None,
                        "retry_id": None,
                        "result": "success",
                        "duration": 5,
                    },
                ],
            }
        ]
    }
    costs = {
        "T1": perf.TaskCost("releng-hardware/mac", None, None),
        "T2": perf.TaskCost("gecko-t/linux", 0.4, 2),
    }
    summary = perf.summarize_push_cost(push, costs)
    assert summary["runs"] == 3
    assert summary["runs_with_pool"] == 2
    assert summary["runs_with_cost"] == 1
    assert summary["machine_seconds_by_pool"] == {
        "releng-hardware/mac": 100,
        "gecko-t/linux": 50,
    }
    assert summary["cost"] == pytest.approx(0.2)


def test_sql_queries_are_parameterized() -> None:
    query = perf.build_alerts_query(
        ("autoland", "mozilla-central"),
        datetime(2026, 1, 2).date(),
        datetime(2026, 2, 1).date(),
    )
    assert "IN ('autoland', 'mozilla-central')" in query
    assert "s.created >= '2026-01-02'" in query
    assert "s.created < '2026-02-01'" in query
    with pytest.raises(ValueError):
        perf.build_alerts_query(
            ("autoland'; DROP TABLE push; --",),
            datetime(2026, 1, 2).date(),
            datetime(2026, 2, 1).date(),
        )


def _alert(**overrides):
    alert = {
        "alert_id": 1,
        "alert_status": 4,
        "is_regression": True,
        "amount_pct": 5.0,
        "t_value": 9.0,
        "noise_profile": "OK",
        "summary_id": 100,
        "summary_status": 7,
        "summary_created": "2026-06-01T00:00:00",
        "bug_number": 555,
        "bug_status": "RESOLVED",
        "push_id": 12,
        "prev_push_id": 11,
        "original_push_id": 12,
        "revision": "c" * 40,
        "push_time": "2026-05-30T00:00:00",
        "prev_push_revision": "b" * 40,
        "repository": "autoland",
        "framework": "browsertime",
        "signature_id": 900,
        "signature_hash": "hash",
        "suite": "amazon",
        "test": "fcp",
        "platform": "linux2404-64-shippable",
        "extra_options": "e10s fission",
        "application": "firefox",
        "summary_tags": None,
        "task_labels": "test-linux2404-64-shippable/opt-browsertime-tp6-firefox-amazon",
        "task_ids": "T1|T2",
    }
    alert.update(overrides)
    return perf.normalize_alert_row(alert)


def _push(push_id, commits, jobs=(), repository="autoland", time="2026-05-30T00:00:00"):
    return {
        "push_id": push_id,
        "repository": repository,
        "revision": commits[-1][0],
        "time": time,
        "author": "a@b.c",
        "commits": [
            {"revision": rev, "desc": desc, "bug_id": bug, "backout": backout}
            for rev, desc, bug, backout in commits
        ],
        "perf_jobs": [
            {
                "label": label,
                "duration": 100,
                "tier": 1,
                "runs": [
                    {
                        "task_id": f"T{push_id}{i}",
                        "retry_id": 0,
                        "result": "success",
                        "duration": 100,
                    }
                ],
            }
            for i, label in enumerate(jobs)
        ],
    }


def _pushes():
    amazon = "test-linux2404-64-shippable/opt-browsertime-tp6-firefox-amazon"
    g1 = "test-linux2404-64-shippable/opt-talos-g1"
    return [
        _push(
            10,
            [("a" * 40, "Bug 1 - x", 1, False)],
            [amazon, g1],
            time="2026-05-28T00:00:00",
        ),
        _push(
            11, [("b" * 40, "Bug 2 - y", 2, False)], [g1], time="2026-05-29T00:00:00"
        ),
        _push(
            12,
            [
                ("c" * 40, "Bug 3 - culprit", 3, False),
                ("d" * 40, "Bug 4 - other", 4, False),
            ],
            [amazon, g1],
        ),
        _push(13, [("e" * 40, "Bug 5 - z", 5, False)], [], time="2026-05-31T00:00:00"),
        _push(
            14,
            [("f" * 40, "Backed out changeset", None, True)],
            [amazon],
            time="2026-06-01T00:00:00",
        ),
    ]


def _bugs():
    return {
        555: {
            "id": 555,
            "regressed_by": [3],
            "status": "RESOLVED",
            "resolution": "FIXED",
            "dupe_of": None,
        }
    }


@pytest.mark.parametrize(
    "test_name, family, application, category",
    [
        ("browsertime-tp6-firefox-amazon", "browsertime-tp6", "firefox", "page-load"),
        ("browsertime-tp6-chrome-amazon", "browsertime-tp6", "chrome", "page-load"),
        ("browsertime-tp6m-fenix-amazon", "browsertime-tp6m", "fenix", "page-load"),
        (
            "browsertime-benchmark-firefox-speedometer3",
            "browsertime-benchmark",
            "firefox",
            "benchmark",
        ),
        (
            "browsertime-benchmark-firefox-motionmark-1-3",
            "browsertime-benchmark",
            "firefox",
            "graphics",
        ),
        (
            "browsertime-benchmark-speedometer3-mobile-fenix-native-profiling",
            "browsertime-benchmark-speedometer3-mobile",
            "fenix",
            "benchmark",
        ),
        (
            "browsertime-firefox-youtube-playback-h264-sfr",
            "browsertime-youtube-playback",
            "firefox",
            "media",
        ),
        (
            "browsertime-network-bench-firefox-h2-download",
            "browsertime-network-bench",
            "firefox",
            "network",
        ),
        (
            "browsertime-responsiveness-firefox-cnn-nav",
            "browsertime-responsiveness",
            "firefox",
            "responsiveness",
        ),
        (
            "browsertime-benchmark-safari-tp-speedometer3",
            "browsertime-benchmark",
            "safari-tp",
            "benchmark",
        ),
        ("talos-g1", "talos-g1", "firefox", "graphics"),
        ("talos-g1-profiling", "talos-g1", "firefox", "graphics"),
        ("talos-damp-inspector", "talos-damp-inspector", "firefox", "devtools"),
        ("talos-other", "talos-other", "firefox", "startup"),
        (
            "talos-perf-reftest-singletons",
            "talos-perf-reftest-singletons",
            "firefox",
            "graphics",
        ),
        ("awsy-base-dmd", "awsy", "firefox", "memory"),
        (
            "perftest-android-hw-a55-aarch64-shippable-startup-fenix-cold-main-first-frame",
            "perftest-startup",
            "fenix",
            "startup",
        ),
        (
            "perftest-linux-domcount-linux2404-64-shippable/opt",
            "perftest",
            "firefox",
            "other",
        ),
    ],
)
def test_grouping_hierarchy(test_name, family, application, category) -> None:
    grouping = perf.split_test_name(test_name)
    assert grouping["family"] == family
    assert grouping["application"] == application
    assert perf.get_test_category(test_name) == category


def test_runnable_identity_levels() -> None:
    identity = perf.get_runnable_identity(
        "test-windows11-64-24h2-shippable/opt-browsertime-tp6-firefox-amazon"
    )
    assert identity["test_name"] == "browsertime-tp6-firefox-amazon"
    assert identity["family"] == "browsertime-tp6"
    assert identity["category"] == "page-load"
    assert identity["application"] == "firefox"
    assert identity["platform_family"] == "windows"
    assert identity["variant"] is None


def test_exclusion_reasons() -> None:
    assert (
        perf.get_exclusion_reason("build-linux64/opt", framework="build_metrics")
        == "framework"
    )
    assert (
        perf.get_exclusion_reason(
            "test-linux2404-64-shippable/opt-browsertime-tp6-chrome-amazon"
        )
        == "application"
    )
    assert (
        perf.get_exclusion_reason("test-linux2404-64-shippable/opt-talos-g1-profiling")
        == "variant"
    )
    assert (
        perf.get_exclusion_reason(
            "test-linux2404-64-shippable/opt-browsertime-tp6-profiling-firefox-amazon"
        )
        == "variant"
    )
    assert (
        perf.get_exclusion_reason(
            "test-linux2404-64-shippable/opt-browsertime-regression-tests-firefox-constant"
        )
        == "canary"
    )
    assert perf.get_exclusion_reason("test-linux2404-64-shippable/opt-talos-g1") is None
    # Only the variants listed in the policy are excluded; swr is a real Firefox run.
    assert (
        perf.get_exclusion_reason("test-linux2404-64-shippable/opt-talos-g1-swr")
        is None
    )
    assert (
        perf.get_exclusion_reason(
            "test-android-hw-a55-14-0-aarch64-shippable/opt-browsertime-tp6m-fenix-amazon"
        )
        is None
    )
    # Signature application only matters when the label carries none.
    assert (
        perf.get_exclusion_reason(
            "test-linux2404-64-shippable/opt-talos-g1", application="chrome"
        )
        is None
    )


def test_generate_regressions_applies_exclusions() -> None:
    alerts = [
        _alert(),
        _alert(
            alert_id=2,
            signature_id=901,
            framework="build_metrics",
            task_labels="build-linux64/opt",
        ),
        _alert(
            alert_id=3,
            signature_id=902,
            application="chrome",
            task_labels="test-linux2404-64-shippable/opt-browsertime-tp6-chrome-amazon",
        ),
        # A summary made only of excluded runnables is dropped entirely.
        _alert(
            alert_id=4,
            summary_id=105,
            signature_id=903,
            push_id=13,
            prev_push_id=12,
            task_labels="test-linux2404-64-shippable/opt-talos-g1-profiling",
        ),
    ]
    push_index = perf.PushIndex(_pushes())
    regressions = list(perf.generate_regressions(alerts, push_index, _bugs(), SETTLED))
    assert [r["summary_id"] for r in regressions] == [100]
    labels = [r["label"] for r in regressions[0]["runnables"]]
    assert labels == ["test-linux2404-64-shippable/opt-browsertime-tp6-firefox-amazon"]
    assert regressions[0]["runnables"][0]["family"] == "browsertime-tp6"
    assert regressions[0]["runnables"][0]["category"] == "page-load"
    assert regressions[0]["policy_version"] == perf.POLICY_VERSION


def test_landings_group_commits_by_bug() -> None:
    push = _push(
        12,
        [
            ("c" * 40, "Bug 3 - part 1", 3, False),
            ("d" * 40, "Bug 4 - other", 4, False),
            ("e" * 40, "Bug 3 - part 2", 3, False),
            ("f" * 40, "Backed out changeset", None, True),
            ("g" * 40, "No Bug - l10n", None, False),
        ],
    )
    result = perf.group_landings(push)
    assert [
        (x["landing_id"], x["bug_id"], x["backout"], x["revisions"]) for x in result
    ] == [
        ("12:3", 3, False, ["c" * 40, "e" * 40]),
        ("12:4", 4, False, ["d" * 40]),
        (f"12:{'f' * 12}", None, True, ["f" * 40]),
        (f"12:{'g' * 12}", None, False, ["g" * 40]),
    ]
    commits = perf.get_push_commits(push)
    assert [
        (c["node"][0], c["landing_id"], c["landing_size"], c["backout"])
        for c in commits
    ] == [
        ("c", "12:3", 2, False),
        ("d", "12:4", 1, False),
        ("e", "12:3", 2, False),
        ("f", f"12:{'f' * 12}", 1, True),
        ("g", f"12:{'g' * 12}", 1, False),
    ]


def test_pin_culprit_commits_rules() -> None:
    multi = _push(
        12,
        [
            ("c" * 40, "Bug 3 - culprit", 3, False),
            ("d" * 40, "Bug 4 - other", 4, False),
        ],
    )
    single = _push(
        20,
        [
            ("a" * 40, "Bug 7 - part 1", 7, False),
            ("b" * 40, "Bug 7 - part 2", 7, False),
        ],
    )
    assert perf.pin_culprit_commits(multi, {"regressed_by": [3]}, 555) == (
        ["c" * 40],
        "bug",
    )
    # The summary links the culprit bug directly instead of a regression bug.
    assert perf.pin_culprit_commits(multi, {"regressed_by": []}, 4) == (
        ["d" * 40],
        "summary_bug",
    )
    assert perf.pin_culprit_commits(multi, None, None) == ([], None)
    # A stack for one bug is one landing and pins as a whole.
    assert perf.pin_culprit_commits(single, None, 999) == (
        ["a" * 40, "b" * 40],
        "single_landing",
    )
    assert perf.pin_culprit_commits(None, None, None) == ([], None)


def test_generate_regressions_labels_and_pins_culprit() -> None:
    alerts = [
        _alert(),
        # Second alert on the same summary, another test.
        _alert(
            alert_id=2,
            signature_id=901,
            suite="g1",
            framework="talos",
            task_labels="test-linux2404-64-shippable/opt-talos-g1",
        ),
        # Invalid alert inside the summary must be dropped.
        _alert(
            alert_id=3,
            alert_status=3,
            signature_id=902,
            task_labels="test-linux2404-64-shippable/opt-talos-g3",
        ),
        # Improvement summary is not a regression.
        _alert(
            alert_id=4, summary_id=101, summary_status=4, push_id=13, prev_push_id=12
        ),
        # Confirmed, reassigned from another push, unpinned (backout-only push).
        _alert(
            alert_id=5,
            summary_id=102,
            summary_status=8,
            push_id=14,
            prev_push_id=10,
            original_push_id=13,
            bug_number=None,
        ),
        # A push still inside the settle window is skipped.
        _alert(alert_id=6, summary_id=103, push_time="2031-01-01T00:00:00"),
    ]
    push_index = perf.PushIndex(_pushes())
    regressions = list(perf.generate_regressions(alerts, push_index, _bugs(), SETTLED))
    assert [r["summary_id"] for r in regressions] == [100, 102]

    first = regressions[0]
    assert first["revisions"] == ["c" * 40, "d" * 40]
    assert first["culprit_commits"] == ["c" * 40]
    assert first["pinned_by"] == "bug"
    assert first["landings"] == [
        {
            "landing_id": "12:3",
            "bug_id": 3,
            "backout": False,
            "revisions": ["c" * 40],
            "culprit": True,
        },
        {
            "landing_id": "12:4",
            "bug_id": 4,
            "backout": False,
            "revisions": ["d" * 40],
            "culprit": False,
        },
    ]
    assert first["regressed_by_agrees"] is True
    assert first["resolved"] is True
    assert first["reassigned"] is False
    assert "tier" not in first and "weight" not in first and "range_size" not in first
    assert sorted(r["label"] for r in first["runnables"]) == [
        "test-linux2404-64-shippable/opt-browsertime-tp6-firefox-amazon",
        "test-linux2404-64-shippable/opt-talos-g1",
    ]

    second = regressions[1]
    assert second["reassigned"] is True
    # A backout-only push: the revert is its single landing and is the culprit.
    assert second["culprit_commits"] == ["f" * 40]
    assert second["pinned_by"] == "single_landing"
    assert second["landings"][0]["backout"] is True
    assert second["regressed_by_agrees"] is None


def test_pairs_and_push_status_and_negatives() -> None:
    windows = "test-windows11-64-24h2-shippable/opt-browsertime-tp6-firefox-amazon"
    alerts = [
        _alert(),
        # Same test on another platform in the same summary merges into one pair.
        _alert(
            alert_id=2,
            signature_id=901,
            platform="windows11-64-24h2-shippable",
            task_labels=windows,
        ),
        # Unpinned regression on push 13 (single landing, so it pins after all).
        _alert(
            alert_id=5,
            summary_id=102,
            summary_status=8,
            push_id=13,
            prev_push_id=12,
            bug_number=None,
            signature_id=905,
            suite="g1",
            framework="talos",
            task_labels="test-linux2404-64-shippable/opt-talos-g1",
        ),
    ]
    push_index = perf.PushIndex(_pushes())
    regressions = list(perf.generate_regressions(alerts, push_index, _bugs(), SETTLED))

    pairs = list(perf.generate_scheduling_pairs(regressions, push_index, "autoland"))
    assert [(p["push_id"], p["node"][0], p["test_name"]) for p in pairs] == [
        (12, "c", "browsertime-tp6-firefox-amazon"),
        (13, "e", "talos-g1"),
    ]
    amazon = pairs[0]
    assert amazon["platform_families"] == ["linux", "windows"]
    assert amazon["landing_id"] == "12:3" and amazon["landing_size"] == 1
    assert amazon["pinned_by"] == "bug"
    assert amazon["family"] == "browsertime-tp6" and amazon["category"] == "page-load"
    assert amazon["summary_ids"] == [100]
    # The innocent landing of the culprit push gets no positive row.
    assert not any(p["node"] == "d" * 40 for p in pairs)
    assert pairs[1]["pinned_by"] == "single_landing"

    statuses = list(
        perf.generate_push_status(push_index, regressions, "autoland", SETTLED)
    )
    by_id = {s["push_id"]: s for s in statuses}
    assert by_id[12]["label_state"] == "culprit" and by_id[10]["label_state"] == "clean"
    assert by_id[12]["identities_ran"] == ["browsertime-tp6-firefox-amazon", "talos-g1"]
    assert by_id[12]["platform_families_ran"] == ["linux"]
    assert [c["node"][0] for c in by_id[12]["commits"]] == ["c", "d"]
    assert [(c["node"][0], c["backout"]) for c in by_id[14]["commits"]] == [("f", True)]
    assert all(s["settled"] for s in statuses)
    assert "ambiguous" not in by_id[12]

    db.write(perf.PERF_PAIRS_DB, pairs)
    db.write(perf.PERF_PUSH_STATUS_DB, statuses)
    rows = list(perf.iter_labeled_pairs())
    labeled = {(r["node"][0], r["test_name"]): r["label"] for r in rows}
    assert labeled[("c", "browsertime-tp6-firefox-amazon")] == 1
    assert labeled[("e", "talos-g1")] == 1
    # Culprit pushes yield only their positives, never negatives.
    assert ("c", "talos-g1") not in labeled and ("d", "talos-g1") not in labeled
    assert ("e", "browsertime-tp6-firefox-amazon") not in labeled
    # Clean pushes yield a negative per non-backout commit and candidate identity.
    assert labeled[("a", "talos-g1")] == 0
    assert not any(k[0] == "f" for k in labeled)
    assert labeled[("b", "browsertime-tp6-firefox-amazon")] == 0
    assert all("landing_id" in r and "push_id" in r for r in rows)


def test_export_selects_commits_and_attaches_patches(monkeypatch) -> None:
    push_index = perf.PushIndex(_pushes())
    regressions = list(
        perf.generate_regressions([_alert()], push_index, _bugs(), SETTLED)
    )
    statuses = list(
        perf.generate_push_status(push_index, regressions, "autoland", SETTLED)
    )
    selected = perf.select_export_commits(
        regressions, statuses, negative_ratio=2, seed=1
    )
    positives = [r for r in selected if r["label"] == 1]
    negatives = [r for r in selected if r["label"] == 0]
    assert [p["node"] for p in positives] == ["c" * 40]
    assert positives[0]["pinned_by"] == "bug"
    assert positives[0]["landing_id"] == "12:3" and positives[0]["landing_size"] == 1
    assert positives[0]["summary_ids"] == [100]
    assert "weight" not in positives[0] and "tier" not in positives[0]
    assert len(negatives) == 2
    # Backouts and every commit of a culprit push are never negatives.
    assert all(n["node"] not in ("c" * 40, "d" * 40, "f" * 40) for n in negatives)

    perf.assign_train_test_splits(selected, test_fraction=0.3)
    assert [r["split"] for r in selected] == ["train", "train", "test"]

    def fake_patches(repo_dir, revs):
        return [
            (
                b"# HG changeset patch\n# User a@b.c\n# Date 1 0\n# Node ID "
                + rev
                + b"\n# Parent 0\nBug 3 - culprit\n\nMore text\n\ndiff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n"
            )
            for rev in revs
        ]

    monkeypatch.setattr(regression_commits, "export_hg_patches", fake_patches)
    exported = list(perf.attach_commit_patches(selected, "/nonexistent"))
    assert len(exported) == 3
    assert exported[-1]["commit_message"] == "Bug 3 - culprit\n\nMore text"
    assert exported[-1]["diff"].startswith("diff --git a/f b/f")
    assert exported[-1]["policy_version"] == perf.POLICY_VERSION


def test_revert_is_pinned_through_the_summary_bug() -> None:
    # Real case: a revert caused the regression. The sheriff linked the reverted
    # bug, whose regressed_by describes that bug's own history, not the revert.
    revert = _push(
        30,
        [
            (
                "9" * 40,
                'Revert "Bug 2067590 - Set the max dirty page modifier" r=smaug',
                2067590,
                True,
            )
        ],
        ["test-linux2404-64-shippable/opt-talos-g1"],
    )
    push_index = perf.PushIndex(_pushes() + [revert])
    bugs = {2067590: {"id": 2067590, "regressed_by": [1947687]}}
    alerts = [_alert(summary_id=200, push_id=30, prev_push_id=14, bug_number=2067590)]
    (regression,) = perf.generate_regressions(alerts, push_index, bugs, SETTLED)
    assert regression["culprit_commits"] == ["9" * 40]
    assert regression["pinned_by"] == "summary_bug"
    assert regression["regressed_by_agrees"] is False

    pairs = list(perf.generate_scheduling_pairs([regression], push_index, "autoland"))
    assert [(p["node"][0], p["backout"], p["test_name"]) for p in pairs] == [
        ("9", True, "browsertime-tp6-firefox-amazon")
    ]
    statuses = list(
        perf.generate_push_status(push_index, [regression], "autoland", SETTLED)
    )
    exported = perf.select_export_commits(
        [regression], statuses, negative_ratio=1, seed=0
    )
    assert [e["node"][0] for e in exported if e["label"] == 1] == ["9"]
    assert all(not e["backout"] for e in exported if e["label"] == 0)


def test_regression_window_is_bounded_by_push_time() -> None:
    push_index = perf.PushIndex(_pushes())
    cutoff = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)
    # A summary opened after the cutoff still counts when its push is settled.
    late_summary = _alert(summary_created="2031-01-01T00:00:00")
    kept = perf.generate_regressions([late_summary], push_index, _bugs(), cutoff)
    assert [r["summary_id"] for r in kept] == [100]
    # Pushes at or after the cutoff, or before --since, are outside the window.
    push_time = datetime(2026, 5, 30, tzinfo=timezone.utc)
    assert not list(
        perf.generate_regressions([late_summary], push_index, _bugs(), push_time)
    )
    assert not list(
        perf.generate_regressions(
            [late_summary], push_index, _bugs(), cutoff, pushed_from=cutoff
        )
    )
    assert perf.is_within_window("2026-05-30T00:00:00", push_time, cutoff)
    assert not perf.is_within_window(None, None, cutoff)


def test_labeled_window_excludes_pushes_before_the_alerts_window() -> None:
    labeled_from = datetime(2026, 5, 29, 12, tzinfo=timezone.utc)
    push_index = perf.PushIndex(_pushes())
    # Pushes 10 (May 28) and 11 (May 29 00:00) predate the alerts window.
    statuses = list(
        perf.generate_push_status(push_index, [], "autoland", SETTLED, labeled_from)
    )
    assert [(s["push_id"], s["settled"]) for s in statuses] == [
        (10, False),
        (11, False),
        (12, True),
        (13, True),
        (14, True),
    ]
    assert [
        (s["push_id"], c["node"][0]) for s, c in perf.iter_clean_commits(statuses)
    ] == [
        (12, "c"),
        (12, "d"),
        (13, "e"),
    ]
    db.write(perf.PERF_PAIRS_DB, [])
    db.write(perf.PERF_PUSH_STATUS_DB, statuses)
    assert perf.read_push_status("autoland") == statuses
    negatives = {r["node"][0] for r in perf.iter_labeled_pairs({"talos-g1"})}
    assert negatives == {"c", "d", "e"}  # a, b are unlabeled; f is a backout

    exported = perf.select_export_commits([], statuses, negative_ratio=1, seed=0)
    assert exported == []  # no positives, so no negatives are sampled


def _bug(bug_id, status="RESOLVED", resolution=None, regressed_by=(), dupe_of=None):
    return {
        "id": bug_id,
        "status": status,
        "resolution": resolution,
        "regressed_by": list(regressed_by),
        "dupe_of": dupe_of,
    }


@pytest.mark.parametrize(
    "summary_status, bug, pinned_by, expected",
    [
        ("investigating", _bug(1, resolution="FIXED"), "bug", "concluded"),
        ("investigating", _bug(1, resolution="WONTFIX"), "bug", "concluded"),
        ("investigating", _bug(1, resolution="WORKSFORME"), "bug", "concluded"),
        ("investigating", _bug(1, resolution="INVALID"), "bug", "rejected"),
        ("investigating", _bug(1, resolution="INACTIVE"), None, "rejected"),
        ("fixed", _bug(1, resolution="INVALID"), "bug", "rejected"),
        ("investigating", _bug(1, status="NEW"), "bug", "pending"),
        ("investigating", _bug(1, status="ASSIGNED"), None, "pending"),
        ("backedout", _bug(1, status="REOPENED"), "bug", "attributed"),
        ("investigating", _bug(1, resolution="DUPLICATE", dupe_of=9), None, "pending"),
        ("investigating", None, None, "pending"),
        ("wontfix", None, None, "attributed"),
        ("investigating", _bug(1, status="NEW"), "summary_bug", "attributed"),
    ],
)
def test_regression_outcome(summary_status, bug, pinned_by, expected) -> None:
    assert perf.decide_regression_outcome(summary_status, bug, pinned_by) == expected


def test_canonical_bug_follows_duplicates() -> None:
    bugs = {
        1: _bug(1, resolution="DUPLICATE", dupe_of=2),
        2: _bug(2, resolution="DUPLICATE", dupe_of=3),
        3: _bug(3, resolution="FIXED", regressed_by=[3]),
        4: _bug(4, resolution="DUPLICATE", dupe_of=99),
    }
    assert perf.resolve_canonical_bug(bugs[1], bugs) == (bugs[3], 3)
    assert perf.resolve_canonical_bug(bugs[3], bugs) == (bugs[3], None)
    assert perf.resolve_canonical_bug(bugs[4], bugs) == (
        bugs[4],
        None,
    )  # target not fetched
    assert perf.resolve_canonical_bug(None, bugs) == (None, None)


def test_generate_regressions_outcomes_and_label_states() -> None:
    bugs = {
        555: _bug(555, resolution="FIXED", regressed_by=[3]),
        600: _bug(600, resolution="DUPLICATE", dupe_of=601),
        601: _bug(601, resolution="WONTFIX", regressed_by=[5]),
        700: _bug(700, status="NEW", regressed_by=[1]),
        800: _bug(800, resolution="INVALID", regressed_by=[2]),
    }
    alerts = [
        _alert(),  # push 12, bug 555 FIXED -> concluded
        # Duplicate whose canonical bug is WONTFIX and pins push 13's commit.
        _alert(
            alert_id=2,
            summary_id=101,
            push_id=13,
            prev_push_id=12,
            bug_number=600,
            signature_id=901,
        ),
        # Open bug on an investigating summary, push 10 -> pending.
        _alert(
            alert_id=3,
            summary_id=102,
            summary_status=5,
            push_id=10,
            prev_push_id=9,
            bug_number=700,
            signature_id=902,
        ),
        # Rejected on push 11.
        _alert(
            alert_id=4,
            summary_id=103,
            push_id=11,
            prev_push_id=10,
            bug_number=800,
            signature_id=903,
        ),
        # Harness-tagged summaries are regressions now; infra-tagged are not.
        _alert(
            alert_id=5,
            summary_id=104,
            push_id=14,
            prev_push_id=13,
            bug_number=None,
            summary_status=8,
            summary_tags="harness",
            signature_id=904,
        ),
        _alert(
            alert_id=6,
            summary_id=105,
            push_id=14,
            prev_push_id=13,
            bug_number=None,
            summary_status=8,
            summary_tags="infra",
            signature_id=905,
        ),
    ]
    push_index = perf.PushIndex(_pushes())
    regressions = list(perf.generate_regressions(alerts, push_index, bugs, SETTLED))
    by_summary = {r["summary_id"]: r for r in regressions}
    assert sorted(by_summary) == [100, 101, 102, 103, 104]

    assert by_summary[100]["outcome"] == "concluded"
    assert by_summary[100]["bug_resolution"] == "FIXED"
    dup = by_summary[101]
    assert dup["outcome"] == "concluded" and dup["canonical_bug"] == 601
    assert dup["bug_resolution"] == "WONTFIX"
    assert dup["culprit_commits"] == ["e" * 40] and dup["pinned_by"] == "bug"
    assert by_summary[102]["outcome"] == "pending"
    assert by_summary[103]["outcome"] == "rejected"
    assert by_summary[104]["outcome"] == "attributed"
    assert by_summary[104]["summary_tags"] == ["harness"]

    states = perf.get_push_label_states(regressions, "autoland")
    assert states == {
        12: "culprit",
        13: "culprit",
        10: "pending",
        11: "rejected",
        14: "culprit",
    }

    # Pairs come only from positives.
    pairs = list(perf.generate_scheduling_pairs(regressions, push_index, "autoland"))
    assert sorted({p["push_id"] for p in pairs}) == [12, 13, 14]

    statuses = list(
        perf.generate_push_status(push_index, regressions, "autoland", SETTLED)
    )
    db.write(perf.PERF_PAIRS_DB, pairs)
    db.write(perf.PERF_PUSH_STATUS_DB, statuses)
    labeled = list(perf.iter_labeled_pairs({"talos-g1"}))
    negatives = {r["node"][0] for r in labeled if r["label"] == 0}
    assert (
        negatives == set()
    )  # pushes 10 and 11 are pending/rejected, the rest culprits

    exported = perf.select_export_commits(
        regressions, statuses, negative_ratio=5, seed=0
    )
    assert {e["node"][0] for e in exported if e["label"] == 1} == {"c", "e", "f"}
    assert not any(
        e["label"] == 0 for e in exported
    )  # no clean push left to sample from


@responses.activate
def test_fetch_bugs_follows_duplicates() -> None:
    def reply(request):
        ids = request.params["id"].split(",")
        bugs = []
        for bug_id in ids:
            bug_id = int(bug_id)
            dupe_of = {1: 2, 2: 3}.get(bug_id)
            bugs.append(
                {
                    "id": bug_id,
                    "status": "RESOLVED",
                    "resolution": "DUPLICATE" if dupe_of else "FIXED",
                    "dupe_of": dupe_of,
                    "regressed_by": [],
                }
            )
        return 200, {}, json.dumps({"bugs": bugs})

    responses.add_callback(
        responses.GET,
        extraction.BUGZILLA_REST_URL,
        callback=reply,
        content_type="application/json",
    )
    bugs = {b["id"]: b for b in perf.fetch_bugs([1])}
    assert sorted(bugs) == [1, 2, 3]
    assert bugs[3]["resolution"] == "FIXED"
    requested = [c.request.params["id"] for c in responses.calls]
    assert requested == ["1", "2", "3"]
