# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import gzip

import numpy as np
import orjson
import pytest
import zstandard

from bugbug_http import models
from bugbug_http.app import API_TOKEN, JobInfo


def _response_json(response):
    if response.headers.get("Content-Encoding") == "gzip":
        return orjson.loads(gzip.decompress(response.data))
    return response.json


def test_endpoint_queues_and_returns_prediction(client, jobs, add_result) -> None:
    endpoint = "/perfregressionpredictor/predict/phabricator/789012"

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
        "revision_id": 123456,
        "diff_id": 789012,
        "prob": [0.25, 0.75],
        "class": 1,
        "risk_score": 0.75,
        "extra_data": {"calibrated": False},
    }
    keys = next(iter(jobs.values()))
    add_result(keys[0], prediction)

    response = client.get(endpoint, headers={API_TOKEN: "test"})
    assert response.status_code == 200
    assert _response_json(response) == prediction


def test_worker_uses_diff_commit_metadata(monkeypatch) -> None:
    class FakePatch:
        def __init__(self, diff_id):
            assert diff_id == 789012
            self.revision_id = 123456
            self.commit_messages = ["[PATCH] - Make rendering faster"]
            self.patch_title = "Unused title"
            self.patch_description = "Unused summary"
            self.raw_diff = "diff --git a/a b/a\n"

        def is_accessible(self):
            return True

        def is_public(self):
            return True

    class FakeModel:
        def __init__(self):
            self.items = None

        def classify(self, items, probabilities=False):
            assert probabilities
            self.items = items
            return np.array([[0.2, 0.8]])

        def get_extra_data(self):
            return {"calibrated": False}

    fake_model = FakeModel()
    monkeypatch.setattr(models, "PhabricatorPatch", FakePatch)
    monkeypatch.setattr(
        models.MODEL_CACHE,
        "get",
        lambda model_name: fake_model,
    )

    assert models.classify_perf_regression(789012) == "OK"
    assert fake_model.items == [
        {
            "commit_message": "Make rendering faster",
            "diff": "diff --git a/a b/a\n",
        }
    ]

    job = JobInfo(models.classify_perf_regression, 789012)
    stored = models.redis.get(job.result_key)
    assert stored is not None
    result = orjson.loads(zstandard.ZstdDecompressor().decompress(stored))
    assert result["revision_id"] == 123456
    assert result["diff_id"] == 789012
    assert result["risk_score"] == 0.8
    assert result["class"] == 1
    assert result["extra_data"]["commit_message_source"] == "diff_metadata"
    assert result["extra_data"]["commit_message_count"] == 1


def test_worker_marks_inaccessible_diff_unavailable(monkeypatch) -> None:
    class FakePatch:
        def __init__(self, diff_id):
            assert diff_id == 789012

        def is_accessible(self):
            return False

        def is_public(self):
            raise AssertionError("is_public should not be called")

    monkeypatch.setattr(models, "PhabricatorPatch", FakePatch)

    assert models.classify_perf_regression(789012) == "OK"
    job = JobInfo(models.classify_perf_regression, 789012)
    stored = models.redis.get(job.result_key)
    assert stored is not None
    result = orjson.loads(stored)
    assert result == {"available": False}


def test_worker_cleans_and_combines_multiple_commit_messages(monkeypatch) -> None:
    class FakePatch:
        revision_id = 123456
        commit_messages = [
            "Bug 123456 - Improve rendering\n\nFirst body.",
            "[PATCH] Bug 789012 - Avoid repeated work\n\nSecond body.",
        ]
        patch_title = "Unused title"
        patch_description = "Unused summary"
        raw_diff = "diff --git a/a b/a\n"

        def __init__(self, diff_id):
            assert diff_id == 789012

        def is_accessible(self):
            return True

        def is_public(self):
            return True

    class FakeModel:
        def classify(self, items, probabilities=False):
            assert items[0]["commit_message"] == (
                "Improve rendering\n\nFirst body.\n\n"
                "Avoid repeated work\n\nSecond body."
            )
            return np.array([[0.3, 0.7]])

        def get_extra_data(self):
            return {}

    monkeypatch.setattr(models, "PhabricatorPatch", FakePatch)
    monkeypatch.setattr(models.MODEL_CACHE, "get", lambda model_name: FakeModel())

    assert models.classify_perf_regression(789012) == "OK"
    job = JobInfo(models.classify_perf_regression, 789012)
    stored = models.redis.get(job.result_key)
    assert stored is not None
    result = orjson.loads(zstandard.ZstdDecompressor().decompress(stored))
    assert result["extra_data"]["commit_message_source"] == "diff_metadata"
    assert result["extra_data"]["commit_message_count"] == 2


def test_worker_falls_back_to_revision_message(monkeypatch) -> None:
    class FakePatch:
        revision_id = 123456
        commit_messages: list[str] = []
        patch_title = "Improve rendering"
        patch_description = "Avoid repeated work."
        raw_diff = "diff --git a/a b/a\n"

        def __init__(self, diff_id):
            assert diff_id == 789012

        def is_accessible(self):
            return True

        def is_public(self):
            return True

    class FakeModel:
        def classify(self, items, probabilities=False):
            assert items[0]["commit_message"] == (
                "Improve rendering\n\nAvoid repeated work."
            )
            return np.array([[0.6, 0.4]])

        def get_extra_data(self):
            return {}

    monkeypatch.setattr(models, "PhabricatorPatch", FakePatch)
    monkeypatch.setattr(models.MODEL_CACHE, "get", lambda model_name: FakeModel())

    assert models.classify_perf_regression(789012) == "OK"
    job = JobInfo(models.classify_perf_regression, 789012)
    stored = models.redis.get(job.result_key)
    assert stored is not None
    result = orjson.loads(zstandard.ZstdDecompressor().decompress(stored))
    assert result["extra_data"]["commit_message_source"] == (
        "revision_title_and_summary_fallback"
    )
    assert result["extra_data"]["commit_message_count"] == 0


def test_worker_propagates_model_loading_failure(monkeypatch) -> None:
    class FakePatch:
        revision_id = 123456
        commit_messages = ["Improve rendering"]
        patch_title = "Unused title"
        patch_description = "Unused summary"

        def __init__(self, diff_id):
            assert diff_id == 789012

        def is_accessible(self):
            return True

        def is_public(self):
            return True

    monkeypatch.setattr(models, "PhabricatorPatch", FakePatch)

    def raise_missing_model(model_name):
        raise FileNotFoundError("missing checkpoint")

    monkeypatch.setattr(models.MODEL_CACHE, "get", raise_missing_model)

    with pytest.raises(FileNotFoundError, match="missing checkpoint"):
        models.classify_perf_regression(789012)

    job = JobInfo(models.classify_perf_regression, 789012)
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
