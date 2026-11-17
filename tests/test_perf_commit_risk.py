# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import contextlib
import re
import time
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pytest
import responses

from bugbug import db
from bugbug.perf import commit_risk, datasets, extraction

A, B, C, D, E = ("a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40)
NULL = "0" * 40
UTC = timezone.utc


class FakeHg:
    """Answers the log and export calls the scorer makes on a clone."""

    def __init__(self, commits, patches=None):
        self.commits = commits
        self.patches = patches or {}
        self.log_calls: list[str] = []

    def rawcommand(self, args):
        options = dict(
            arg.decode().split("=", 1) for arg in args if arg.startswith(b"--")
        )
        revset = options["--rev"]
        self.log_calls.append(revset)
        out = ""
        for node in re.findall(r"present\(([0-9a-f]{40})\)", revset):
            commit = self.commits.get(node)
            if commit is None:
                continue
            parents = commit.get("parents", [B])
            fields = [
                node,
                commit.get("author", "Dev <dev@example.com>"),
                commit["desc"],
                parents[0] if parents else NULL,
                parents[1] if len(parents) > 1 else NULL,
            ]
            out += "\0".join(fields) + "\0"
        return out.encode()

    def export(self, revs, git=True):
        return self.patches[revs[0].decode()]


def test_changesets_from_clone() -> None:
    hg = FakeHg(
        {
            A: {"desc": "Bug 1 - Change", "parents": [B]},
            C: {"desc": "Merge autoland to central", "parents": [A, B]},
            D: {"desc": f"Backed out changeset {A[:12]} (bug 1)", "parents": [C]},
        }
    )
    changesets = commit_risk.changesets_from_clone(hg, [A, C, D, E])
    assert set(changesets) == {A, C, D}
    assert changesets[A] == {
        "node": A,
        "desc": "Bug 1 - Change",
        "author": "Dev <dev@example.com>",
        "parents": [B],
    }
    assert changesets[C]["parents"] == [A, B]
    # A backout is scored like any other commit.
    assert [commit_risk.get_ignore_reason(changesets[node]) for node in (A, C, D)] == [
        None,
        "merge",
        None,
    ]
    assert hg.log_calls == ["+".join(f"present({node})" for node in (A, C, D, E))]
    assert commit_risk.changesets_from_clone(hg, []) == {}


def test_is_current_record() -> None:
    def record(model_version="v1", ignored_reason=None):
        return {
            "node": A,
            "model_version": model_version,
            "ignored_reason": ignored_reason,
        }

    assert commit_risk.is_current_record(record(), "v1")
    assert commit_risk.is_current_record(record(ignored_reason="merge"), "v1")
    assert not commit_risk.is_current_record(record("v0"), "v1")
    # Skipped under a rule that no longer exists: scored again by the next run.
    assert not commit_risk.is_current_record(record(ignored_reason="backout"), "v1")


def _push(push_id, time, nodes, pushlog_id=None):
    return {
        "push_id": push_id,
        "repository": "autoland",
        "revision": nodes[-1],
        "time": time,
        "pushlog_id": pushlog_id,
        "commits": [
            {"revision": node, "desc": "Bug 1 - x", "bug_id": 1, "backout": False}
            for node in nodes
        ],
        "perf_jobs": [],
    }


PUSHES = [
    _push(2, "2026-05-02T00:00:00Z", [C], 12),
    _push(1, "2026-05-01T00:00:00Z", [A, B], 11),
    _push(3, "2026-05-03T00:00:00Z", [D]),
]


def test_select_unscored_commits() -> None:
    selected = commit_risk.select_unscored_commits(PUSHES, {B})
    assert [commit["node"] for commit in selected] == [A, C, D]
    assert selected[0] == {
        "node": A,
        "push_id": 1,
        "pushlog_id": 11,
        "push_time": "2026-05-01T00:00:00Z",
    }
    assert selected[2]["pushlog_id"] is None

    since = datetime(2026, 5, 2, tzinfo=UTC)
    until = datetime(2026, 5, 3, tzinfo=UTC)
    select = commit_risk.select_unscored_commits
    assert [c["node"] for c in select(PUSHES, set(), since, until)] == [C]
    assert [c["node"] for c in select(PUSHES, set(), until=until)] == [A, B, C]
    assert [c["node"] for c in select(PUSHES, set(), since=since)] == [C, D]


class FakeModel:
    def __init__(self, version="perf-regression-predictor-v1"):
        self.version = version
        self.items = []

    def classify(self, items, probabilities=False):
        self.items.extend(items)
        return np.array([[0.25, 0.75]])

    def get_extra_data(self):
        return {
            "model_name": "Perf Regression Predictor",
            "model_version": self.version,
        }


PATCH = (
    b"# HG changeset patch\n# User Dev <dev@example.com>\n# Date 1 0\n# Node ID "
    + A.encode()
    + b"\n# Parent "
    + B.encode()
    + b"\nBug 1 - Speed up\n\nMore details\n\n"
    b"diff --git a/f b/f\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n"
)

COMMITS = [
    {"node": A, "push_id": 1, "pushlog_id": 11, "push_time": "2026-05-01T00:00:00Z"},
    {"node": D, "push_id": 3, "pushlog_id": None, "push_time": "2026-05-03T00:00:00Z"},
    {"node": E, "push_id": 4, "pushlog_id": 14, "push_time": "2026-05-04T00:00:00Z"},
]


def _clone(monkeypatch, hg):
    monkeypatch.setattr(
        commit_risk.hglib, "open", lambda repo_dir: contextlib.nullcontext(hg)
    )


def test_score_commits(monkeypatch) -> None:
    hg = FakeHg(
        {
            A: {"desc": "Bug 1 - Speed up", "parents": [B]},
            D: {"desc": "Merge autoland to central", "parents": [A, B]},
        },
        {A: PATCH},
    )
    _clone(monkeypatch, hg)
    model = FakeModel()
    stats: collections.Counter = collections.Counter()
    records = list(
        commit_risk.score_commits(
            model, "v1", "/repo", COMMITS, chunk_size=2, stats=stats
        )
    )

    assert [record["node"] for record in records] == [A, D]
    scored, ignored = records
    assert scored["ignored_reason"] is None
    assert scored["prob"] == [0.25, 0.75]
    assert scored["class"] == 1 and scored["risk_score"] == 0.75
    assert scored["push_id"] == 1 and scored["pushlog_id"] == 11
    assert scored["push_time"] == "2026-05-01T00:00:00Z"
    assert ignored["ignored_reason"] == "merge"
    assert ignored["prob"] is None and ignored["class"] is None
    assert ignored["risk_score"] is None
    assert all(r["model_version"] == "v1" and r["scored_at"] for r in records)
    # The model gets the message and the diff split from the export.
    assert model.items == [
        {
            "commit_message": "Bug 1 - Speed up\n\nMore details",
            "diff": PATCH.decode()[PATCH.decode().index("diff --git") :],
        }
    ]
    # Three commits in chunks of two take two hg log calls; the commit missing
    # from the clone is counted, not recorded.
    assert len(hg.log_calls) == 2
    assert stats == {"scored": 1, "ignored": 1, "missing": 1}


def test_score_commits_stops_at_the_deadline(monkeypatch) -> None:
    _clone(monkeypatch, FakeHg({A: {"desc": "Bug 1 - x"}}, {A: PATCH}))
    stats: collections.Counter = collections.Counter()
    records = list(
        commit_risk.score_commits(
            FakeModel(),
            "v1",
            "/repo",
            COMMITS,
            deadline=time.monotonic() - 1,
            stats=stats,
        )
    )
    assert records == []
    assert stats == {"unscored": 3}


def test_score_rescores_commits_skipped_under_a_dropped_rule(monkeypatch) -> None:
    from scripts import perf_commit_risk_scorer as scorer

    def previous(node, push_id, day, model_version, ignored_reason, risk_score):
        return {
            "node": node,
            "push_id": push_id,
            "push_time": f"2026-05-0{day}T00:00:00Z",
            "model_version": model_version,
            "ignored_reason": ignored_reason,
            "risk_score": risk_score,
        }

    records = [
        previous(A, 1, 1, "v1", None, 0.5),
        previous(B, 1, 1, "v1", "backout", None),
        previous(C, 2, 2, "v0", None, 0.1),
    ]
    selected: list[str] = []

    def fake_score_commits(model, version, repo_dir, commits, *args):
        selected.extend(commit["node"] for commit in commits)
        for commit in commits:
            yield {**commit, "model_version": version, "risk_score": 0.9}

    written: dict[str, list] = {}
    monkeypatch.setattr(scorer.db, "exists", lambda path: True)
    monkeypatch.setattr(
        scorer.db, "write", lambda path, rows: written.update({path: list(rows)})
    )
    monkeypatch.setattr(scorer.datasets, "load_previous_snapshot", lambda path: True)
    monkeypatch.setattr(scorer.datasets, "get_perf_commit_risk", lambda: iter(records))
    monkeypatch.setattr(scorer.datasets, "get_perf_pushes", lambda: iter(PUSHES))
    monkeypatch.setattr(
        scorer.commit_risk, "load_model", lambda model_dir: FakeModel("v1")
    )
    monkeypatch.setattr(scorer.commit_risk, "score_commits", fake_score_commits)
    monkeypatch.setattr(scorer, "zstd_compress", lambda path: None)

    scorer.score("/repo", until=datetime(2026, 6, 1, tzinfo=UTC))

    # B was skipped under the dropped backout rule and C has no v1 score yet;
    # their new records supersede B's, while A's and C's v0 record stay.
    assert selected == [B, C, D]
    assert [
        (r["node"], r["model_version"], r["risk_score"])
        for r in written[datasets.PERF_COMMIT_RISK_DB]
    ] == [
        (A, "v1", 0.5),
        (B, "v1", 0.9),
        (C, "v0", 0.1),
        (C, "v1", 0.9),
        (D, "v1", 0.9),
    ]


def test_model_version() -> None:
    assert commit_risk.get_model_version(FakeModel()) == "perf-regression-predictor-v1"
    assert commit_risk.get_model_version(FakeModel("2026-10-01")) == "2026-10-01"
    with pytest.raises(ValueError):
        commit_risk.get_model_version(FakeModel(None))


def test_commit_risk_dataset_round_trip() -> None:
    record = {"node": A, "push_id": 1, "model_version": "v1", "risk_score": 0.5}
    db.write(datasets.PERF_COMMIT_RISK_DB, [record])
    assert list(datasets.get_perf_commit_risk()) == [record]


def test_pushlog_windows() -> None:
    def t(day, hour=0):
        return datetime(2026, 5, day, hour, tzinfo=UTC)

    windows = extraction.pushlog_windows([t(10), t(1), t(2, 12), t(20), t(3, 23)], 3)
    assert windows == [(t(1), t(4)), (t(10), t(13)), (t(20), t(23))]
    assert extraction.pushlog_windows([], 3) == []


@responses.activate
def test_fetch_and_set_pushlog_ids() -> None:
    responses.add(
        responses.GET,
        extraction.PUSHLOG_URL,
        json={
            "lastpushid": 3,
            "pushes": {
                "1": {"changesets": [A], "date": 1, "user": "a@b.c"},
                "2": {"changesets": [], "date": 2, "user": "a@b.c"},
                "3": {"changesets": [B, C], "date": 3, "user": "a@b.c"},
            },
        },
    )
    windows = [(datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 5, 4, tzinfo=UTC))]
    pushlog_ids = extraction.fetch_pushlog_ids(windows)
    # Keyed by the head changeset, since that is Treeherder's push revision.
    assert pushlog_ids == {A: 1, C: 3}
    params = responses.calls[0].request.params
    assert params["version"] == "2" and params["tipsonly"] == "1"
    assert params["startdate"] == "2026-04-30 23:00:00"
    assert params["enddate"] == "2026-05-04 01:00:00"

    pushes: list[dict[str, Any]] = [
        {"revision": A, "pushlog_id": None},
        {"revision": C, "pushlog_id": 7},
        {"revision": D},
    ]
    assert extraction.set_pushlog_ids(pushes, pushlog_ids) == 1
    assert [push["pushlog_id"] for push in pushes] == [1, 7, None]
