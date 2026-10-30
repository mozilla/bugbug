# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import gzip

import numpy as np
import orjson
import pytest
import requests
import zstandard

from bugbug_http import models
from bugbug_http.app import API_TOKEN, JobInfo

PATCH_ONE = b"""\
# HG changeset patch
# User Developer <developer@example.com>
# Date 1600000000 0
# Node ID node1hash
# Parent  parent1hash
Bug 123456 - Make rendering faster

diff --git a/widget.py b/widget.py
--- a/widget.py
+++ b/widget.py
@@ -1 +1 @@
-old_value = 1
+new_value = 2
"""

PATCH_TWO = b"""\
# HG changeset patch
# User Developer <developer@example.com>
# Date 1600000001 0
# Node ID node2hash
# Parent  node1hash
Bug 123456 - Avoid repeated work

diff --git a/loop.py b/loop.py
--- a/loop.py
+++ b/loop.py
@@ -1 +1 @@
-slow()
+fast()
"""


def _response_json(response):
    if response.headers.get("Content-Encoding") == "gzip":
        return orjson.loads(gzip.decompress(response.data))
    return response.json


def _changeset(
    node: str,
    desc: str = "Bug 123456 - Change something",
    author: str = "Developer <developer@example.com>",
    parents: list[str] | None = None,
) -> dict:
    """Build a minimal hg.mozilla.org automationrelevance changeset."""
    return {
        "node": node,
        "desc": desc,
        "author": author,
        "parents": parents if parents is not None else ["parentnode"],
    }


def _mock_repo(monkeypatch, changesets, patches_by_node):
    """Mock the push resolution; records the revs exported from the clone."""
    exported_revs: list[list[bytes]] = []
    monkeypatch.setattr(models.repository, "pull", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        models, "get_hgmo_stack_changesets", lambda branch, rev: changesets
    )

    def get_commit_patches(repo_dir, revs):
        exported_revs.append(list(revs))
        return [patches_by_node[rev] for rev in revs]

    monkeypatch.setattr(models.repository, "get_commit_patches", get_commit_patches)
    return exported_revs


def test_endpoint_queues_and_returns_prediction(client, jobs, add_result) -> None:
    endpoint = "/perfregressionpredictor/predict/push/autoland/abc123def456"

    unauthorized = client.get(endpoint)
    assert unauthorized.status_code == 401

    # Only models registered as push classifiers can be used with the push
    # prediction endpoint.
    non_push_model = client.get(
        "/component/predict/push/autoland/abc123def456",
        headers={API_TOKEN: "test"},
    )
    assert non_push_model.status_code == 404

    response = client.get(endpoint, headers={API_TOKEN: "test"})
    assert response.status_code == 202
    assert _response_json(response) == {"ready": False}

    prediction = {
        "branch": "integration/autoland",
        "rev": "abc123def456",
        "risk_score": 0.8,
        "commits": [
            {
                "node": "node1hash",
                "prob": [0.2, 0.8],
                "class": 1,
                "risk_score": 0.8,
                "ignored_reason": None,
            }
        ],
        "extra_data": {
            "calibrated": False,
            "commit_count": 1,
            "ignored_commit_count": 0,
        },
    }
    keys = next(iter(jobs.values()))
    add_result(keys[0], prediction)

    response = client.get(endpoint, headers={API_TOKEN: "test"})
    assert response.status_code == 200
    assert _response_json(response) == prediction


def test_worker_scores_each_commit(monkeypatch) -> None:
    probabilities_by_message = {
        "Bug 123456 - Make rendering faster": np.array([[0.2, 0.8]]),
        "Bug 123456 - Avoid repeated work": np.array([[0.9, 0.1]]),
    }

    class FakeModel:
        def __init__(self):
            self.calls = []

        def classify(self, items, probabilities=False):
            assert probabilities
            # Each commit is scored on its own, one item at a time, to keep
            # peak memory independent of the number of commits in the push.
            assert len(items) == 1
            self.calls.append(items[0])
            return probabilities_by_message[items[0]["commit_message"]]

        def get_extra_data(self):
            return {"calibrated": False}

    fake_model = FakeModel()
    _mock_repo(
        monkeypatch,
        [_changeset("node1hash"), _changeset("node2hash")],
        {b"node1hash": PATCH_ONE, b"node2hash": PATCH_TWO},
    )
    monkeypatch.setattr(models.MODEL_CACHE, "get", lambda model_name: fake_model)

    assert (
        models.classify_perf_regression("integration/autoland", "abc123def456") == "OK"
    )

    # Each commit is scored separately, with its own message and full patch.
    assert fake_model.calls == [
        {
            "commit_message": "Bug 123456 - Make rendering faster",
            "diff": PATCH_ONE.decode("utf-8"),
        },
        {
            "commit_message": "Bug 123456 - Avoid repeated work",
            "diff": PATCH_TWO.decode("utf-8"),
        },
    ]

    job = JobInfo(
        models.classify_perf_regression, "integration/autoland", "abc123def456"
    )
    stored = models.redis.get(job.result_key)
    assert stored is not None
    result = orjson.loads(zstandard.ZstdDecompressor().decompress(stored))
    assert result["branch"] == "integration/autoland"
    assert result["rev"] == "abc123def456"
    # Top-level risk score is the max across commits.
    assert result["risk_score"] == 0.8
    assert result["extra_data"]["commit_count"] == 2
    assert result["extra_data"]["ignored_commit_count"] == 0
    assert result["commits"] == [
        {
            "node": "node1hash",
            "prob": [0.2, 0.8],
            "class": 1,
            "risk_score": 0.8,
            "ignored_reason": None,
        },
        {
            "node": "node2hash",
            "prob": [0.9, 0.1],
            "class": 0,
            "risk_score": 0.1,
            "ignored_reason": None,
        },
    ]


def test_worker_does_not_cache_hgmo_errors(monkeypatch) -> None:
    monkeypatch.setattr(models.repository, "pull", lambda *args, **kwargs: None)

    def raise_hgmo_error(branch, rev):
        raise requests.exceptions.HTTPError("service unavailable")

    monkeypatch.setattr(models, "get_hgmo_stack_changesets", raise_hgmo_error)

    def unexpected_model(model_name):
        raise AssertionError("model should not be loaded when the stack is unknown")

    monkeypatch.setattr(models.MODEL_CACHE, "get", unexpected_model)

    # Like schedule_tests: nothing is stored, so the next poll retries the job.
    assert models.classify_perf_regression("try", "deadbeef") == "NOK"
    job = JobInfo(models.classify_perf_regression, "try", "deadbeef")
    assert models.redis.get(job.result_key) is None


def test_worker_marks_empty_stack_unavailable(monkeypatch) -> None:
    _mock_repo(monkeypatch, [], {})

    def unexpected_model(model_name):
        raise AssertionError("model should not be loaded for an empty stack")

    monkeypatch.setattr(models.MODEL_CACHE, "get", unexpected_model)

    assert models.classify_perf_regression("try", "deadbeef") == "OK"
    job = JobInfo(models.classify_perf_regression, "try", "deadbeef")
    stored = models.redis.get(job.result_key)
    assert stored is not None
    assert orjson.loads(stored) == {"available": False}


def test_worker_propagates_model_loading_failure(monkeypatch) -> None:
    _mock_repo(monkeypatch, [_changeset("node1hash")], {b"node1hash": PATCH_ONE})

    def raise_missing_model(model_name):
        raise FileNotFoundError("missing checkpoint")

    monkeypatch.setattr(models.MODEL_CACHE, "get", raise_missing_model)

    with pytest.raises(FileNotFoundError, match="missing checkpoint"):
        models.classify_perf_regression("integration/autoland", "abc123def456")

    job = JobInfo(
        models.classify_perf_regression, "integration/autoland", "abc123def456"
    )
    assert models.redis.get(job.result_key) is None


class _ScoringModel:
    """Fake model returning a fixed score and counting classify calls."""

    def __init__(self, probabilities):
        self.probabilities = probabilities
        self.calls = 0

    def classify(self, items, probabilities=False):
        self.calls += 1
        return self.probabilities

    def get_extra_data(self):
        return {"calibrated": False}


def test_worker_skips_ignored_commits_with_reason(monkeypatch) -> None:
    fake_model = _ScoringModel(np.array([[0.3, 0.7]]))
    exported_revs = _mock_repo(
        monkeypatch,
        [
            _changeset("mergehash", parents=["p1", "p2"]),
            _changeset("node1hash"),
            _changeset("wptsynchash", author="moz-wptsync-bot <wptsync@mozilla.com>"),
        ],
        {b"node1hash": PATCH_ONE},
    )
    monkeypatch.setattr(models.MODEL_CACHE, "get", lambda model_name: fake_model)

    assert (
        models.classify_perf_regression("integration/autoland", "abc123def456") == "OK"
    )

    # Only the commit to score is exported from the clone and classified.
    assert exported_revs == [[b"node1hash"]]
    assert fake_model.calls == 1

    job = JobInfo(
        models.classify_perf_regression, "integration/autoland", "abc123def456"
    )
    result = orjson.loads(
        zstandard.ZstdDecompressor().decompress(models.redis.get(job.result_key))
    )
    # Ignored commits keep their place in the push, carry a reason and no score,
    # and do not take part in the push-level risk score.
    assert result["risk_score"] == 0.7
    assert result["extra_data"]["commit_count"] == 3
    assert result["extra_data"]["ignored_commit_count"] == 2
    assert result["commits"] == [
        {
            "node": "mergehash",
            "prob": None,
            "class": None,
            "risk_score": None,
            "ignored_reason": "merge",
        },
        {
            "node": "node1hash",
            "prob": [0.3, 0.7],
            "class": 1,
            "risk_score": 0.7,
            "ignored_reason": None,
        },
        {
            "node": "wptsynchash",
            "prob": None,
            "class": None,
            "risk_score": None,
            "ignored_reason": "wptsync",
        },
    ]


def test_worker_reports_fully_ignored_push_without_score(monkeypatch) -> None:
    fake_model = _ScoringModel(np.array([[0.3, 0.7]]))
    exported_revs = _mock_repo(
        monkeypatch,
        [
            _changeset("formathash", desc="Bug 1 - Reformat. # ignore-this-changeset"),
            _changeset("mergehash", parents=["p1", "p2"]),
        ],
        {},
    )
    monkeypatch.setattr(models.MODEL_CACHE, "get", lambda model_name: fake_model)

    assert (
        models.classify_perf_regression("integration/autoland", "abc123def456") == "OK"
    )

    # Nothing is exported or classified, but the client still learns why.
    assert exported_revs == []
    assert fake_model.calls == 0

    job = JobInfo(
        models.classify_perf_regression, "integration/autoland", "abc123def456"
    )
    result = orjson.loads(
        zstandard.ZstdDecompressor().decompress(models.redis.get(job.result_key))
    )
    assert result["risk_score"] is None
    assert result["extra_data"]["commit_count"] == 2
    assert result["extra_data"]["ignored_commit_count"] == 2
    assert [c["ignored_reason"] for c in result["commits"]] == [
        "ignore-this-changeset",
        "merge",
    ]
    assert all(c["risk_score"] is None for c in result["commits"])
