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


def _mock_repo(monkeypatch, revs, patches):
    monkeypatch.setattr(models.repository, "pull", lambda *args, **kwargs: None)
    monkeypatch.setattr(models, "get_hgmo_stack", lambda branch, rev: revs)
    monkeypatch.setattr(
        models.repository, "get_commit_patches", lambda repo_dir, r: patches
    )


def test_endpoint_queues_and_returns_prediction(client, jobs, add_result) -> None:
    endpoint = "/perfregressionpredictor/predict/push/autoland/abc123def456"

    unauthorized = client.get(endpoint)
    assert unauthorized.status_code == 401

    wrong_input_kind = client.get(
        "/perfregressionpredictor/predict/123456",
        headers={API_TOKEN: "test"},
    )
    assert wrong_input_kind.status_code == 404

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
            }
        ],
        "extra_data": {"calibrated": False, "commit_count": 1},
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
    _mock_repo(monkeypatch, [b"node1hash", b"node2hash"], [PATCH_ONE, PATCH_TWO])
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
    assert result["commits"] == [
        {"node": "node1hash", "prob": [0.2, 0.8], "class": 1, "risk_score": 0.8},
        {"node": "node2hash", "prob": [0.9, 0.1], "class": 0, "risk_score": 0.1},
    ]


def test_worker_marks_missing_push_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(models.repository, "pull", lambda *args, **kwargs: None)

    def raise_not_found(branch, rev):
        raise requests.exceptions.HTTPError("not found")

    monkeypatch.setattr(models, "get_hgmo_stack", raise_not_found)

    def unexpected_model(model_name):
        raise AssertionError("model should not be loaded for a missing push")

    monkeypatch.setattr(models.MODEL_CACHE, "get", unexpected_model)

    assert models.classify_perf_regression("try", "deadbeef") == "OK"
    job = JobInfo(models.classify_perf_regression, "try", "deadbeef")
    stored = models.redis.get(job.result_key)
    assert stored is not None
    assert orjson.loads(stored) == {"available": False}


def test_worker_marks_empty_stack_unavailable(monkeypatch) -> None:
    _mock_repo(monkeypatch, [], [])

    def unexpected_model(model_name):
        raise AssertionError("model should not be loaded for an empty stack")

    monkeypatch.setattr(models.MODEL_CACHE, "get", unexpected_model)

    assert models.classify_perf_regression("try", "deadbeef") == "OK"
    job = JobInfo(models.classify_perf_regression, "try", "deadbeef")
    stored = models.redis.get(job.result_key)
    assert stored is not None
    assert orjson.loads(stored) == {"available": False}


def test_worker_propagates_model_loading_failure(monkeypatch) -> None:
    _mock_repo(monkeypatch, [b"node1hash"], [PATCH_ONE])

    def raise_missing_model(model_name):
        raise FileNotFoundError("missing checkpoint")

    monkeypatch.setattr(models.MODEL_CACHE, "get", raise_missing_model)

    with pytest.raises(FileNotFoundError, match="missing checkpoint"):
        models.classify_perf_regression("integration/autoland", "abc123def456")

    job = JobInfo(
        models.classify_perf_regression, "integration/autoland", "abc123def456"
    )
    assert models.redis.get(job.result_key) is None


def test_load_model_uses_registered_model_class_and_standard_directory(
    monkeypatch,
) -> None:
    loaded_directories: list[str] = []
    sentinel = object()

    class FakeModel:
        @staticmethod
        def load(model_directory):
            loaded_directories.append(model_directory)
            return sentinel

    monkeypatch.setattr(
        models,
        "get_model_class",
        lambda model_name: FakeModel,
    )

    loaded_model = models.load_model("somecustommodel")
    assert loaded_model is sentinel
    assert loaded_directories == ["somecustommodelmodel"]
