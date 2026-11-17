# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import gzip

import hglib
import numpy as np
import orjson
import zstandard

from bugbug_http import models
from bugbug_http.app import API_TOKEN, JobInfo

REV1 = "a" * 40
REV2 = "b" * 40


def _response_json(response):
    if response.headers.get("Content-Encoding") == "gzip":
        return orjson.loads(gzip.decompress(response.data))
    return response.json


def _job_key(rev: str, branch: str = "integration/autoland") -> str:
    return str(JobInfo(models.classify_commits, "regressor", branch, rev))


def _get_result(rev: str, branch: str = "integration/autoland"):
    job = JobInfo(models.classify_commits, "regressor", branch, rev)
    return orjson.loads(
        zstandard.ZstdDecompressor().decompress(models.redis.get(job.result_key))
    )


PREDICTION = {
    "node": REV2,
    "bug_id": 123456,
    "prob": [0.9, 0.1],
    "index": 0,
    "class": 0,
    "risk_band": "l",
    "ignored_reason": None,
    "extra_data": {},
}


def test_commit_endpoint(client, jobs, add_result) -> None:
    endpoint = f"/regressor/predict/commit/autoland/{REV2}"

    assert client.get(endpoint).status_code == 401

    # Only commit models can be used with the commit prediction endpoint.
    response = client.get(
        f"/component/predict/commit/autoland/{REV2}", headers={API_TOKEN: "test"}
    )
    assert response.status_code == 404

    for rev in ("tip", REV2[:12]):
        response = client.get(
            f"/regressor/predict/commit/autoland/{rev}", headers={API_TOKEN: "test"}
        )
        assert response.status_code == 400

    response = client.get(endpoint, headers={API_TOKEN: "test"})
    assert response.status_code == 202
    assert _response_json(response) == {"ready": False}
    assert [keys[0] for keys in jobs.values()] == [_job_key(REV2)]

    # The commit is not queued again while it is being processed.
    response = client.get(endpoint, headers={API_TOKEN: "test"})
    assert response.status_code == 202
    assert len(jobs) == 1

    add_result(_job_key(REV2), PREDICTION)

    response = client.get(endpoint, headers={API_TOKEN: "test"})
    assert response.status_code == 200
    assert _response_json(response) == PREDICTION


def test_commits_endpoint(client, jobs, add_result) -> None:
    endpoint = "/regressor/predict/commits/autoland"

    assert client.post(endpoint, json={"commits": [REV1]}).status_code == 401

    bodies: tuple[object, ...] = (
        {},
        {"commits": []},
        {"commits": ["not a hash"]},
        {"commits": [REV1[:12]]},
        {"commits": [REV1] * 101},
        [REV1],
    )
    for body in bodies:
        response = client.post(endpoint, json=body, headers={API_TOKEN: "test"})
        assert response.status_code == 400, body

    add_result(_job_key(REV2), PREDICTION)

    response = client.post(
        endpoint, json={"commits": [REV1, REV2, REV1]}, headers={API_TOKEN: "test"}
    )
    assert response.status_code == 202
    assert _response_json(response) == {
        "commits": {REV1: {"ready": False}, REV2: PREDICTION}
    }
    # Only the missing commit is queued.
    assert [keys[0] for keys in jobs.values()] == [_job_key(REV1)]

    add_result(_job_key(REV1), {"available": False})

    response = client.post(
        endpoint, json={"commits": [REV1, REV2]}, headers={API_TOKEN: "test"}
    )
    assert response.status_code == 200
    assert _response_json(response) == {
        "commits": {REV1: {"available": False}, REV2: PREDICTION}
    }


class MockRegressorModel:
    def __init__(self):
        self.classified = []

    class le:
        @staticmethod
        def inverse_transform(indexes):
            return indexes

    def classify(self, commits, probabilities):
        self.classified.extend(commits)
        return np.array([[0.25, 0.75]] * len(commits))

    def find_risk_band(self, risk):
        return "h" if risk > 0.5 else "l"

    def get_extra_data(self):
        return {"model_version": "1"}


def test_classify_commits(
    monkeypatch,
    mock_repo,
    mock_hgmo,
    mock_component_taskcluster_artifact,
    mock_coverage_mapping_artifact,
) -> None:
    local_dir, remote_dir = mock_repo
    with hglib.open(str(remote_dir)) as hg:
        remote_node = hg.log(limit=1)[0].node.decode("ascii")
    with hglib.open(str(local_dir)) as hg:
        local_node = hg.log(limit=1)[0].node.decode("ascii")

    model = MockRegressorModel()
    monkeypatch.setattr(models.MODEL_CACHE, "get", lambda model_name: model)

    unknown = "c" * 40
    pulled = []
    orig_pull = models.repository.pull

    def pull(repo_dir, branch, rev, update):
        pulled.append(rev)
        if rev == unknown:
            raise RuntimeError("unknown revision")
        orig_pull(repo_dir, branch, rev, update)

    monkeypatch.setattr(models.repository, "pull", pull)

    assert (
        models.classify_commits(
            "regressor", "integration/autoland", [remote_node, local_node, unknown]
        )
        == "OK"
    )

    assert pulled == [remote_node, local_node, unknown]
    with hglib.open(str(local_dir)) as hg:
        assert hg.log(limit=1)[0].node.decode("ascii") == remote_node

    assert sorted(commit["node"] for commit in model.classified) == sorted(
        [remote_node, local_node]
    )
    for node in (remote_node, local_node):
        assert _get_result(node) == {
            "node": node,
            "bug_id": None,
            "prob": [0.25, 0.75],
            "index": 1,
            "class": 1,
            "risk_band": "h",
            "ignored_reason": None,
            "extra_data": {"model_version": "1"},
        }
    assert _get_result(unknown) == {"available": False}


def test_classify_commits_not_scored(monkeypatch) -> None:
    nodes = {
        "backout": "1" * 40,
        "ignored": "2" * 40,
        "scored": "3" * 40,
        "merge": "4" * 40,
    }
    monkeypatch.setattr(models.repository, "pull", lambda *args, **kwargs: None)

    def download_commits(repo_dir, revs, **kwargs):
        # Merge commits are not mined.
        return tuple(
            {
                "node": node,
                "bug_id": 1,
                "backsout": ["0" * 40] if name == "backout" else [],
                "ignored": name == "ignored",
            }
            for name, node in nodes.items()
            if name != "merge"
        )

    monkeypatch.setattr(models.repository, "download_commits", download_commits)
    model = MockRegressorModel()
    monkeypatch.setattr(models.MODEL_CACHE, "get", lambda model_name: model)

    assert models.classify_commits("regressor", "try", list(nodes.values())) == "OK"

    assert [commit["node"] for commit in model.classified] == [nodes["scored"]]
    for reason in ("backout", "ignored"):
        result = _get_result(nodes[reason], "try")
        assert result["ignored_reason"] == reason
        assert result["prob"] is None
        assert result["risk_band"] is None
    assert _get_result(nodes["scored"], "try")["prob"] == [0.25, 0.75]
    assert _get_result(nodes["merge"], "try") == {"available": False}
