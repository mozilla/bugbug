# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import contextlib
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
        # Added to .hg-annotate-ignore-revs after it was added to the commits DB.
        "newly_ignored": "5" * 40,
        "backedout": "6" * 40,
        # Backed out after it was added to the commits DB.
        "newly_backedout": "7" * 40,
        # Backed out when it was mined.
        "mined_backedout": "8" * 40,
    }
    monkeypatch.setattr(models.repository, "pull", lambda *args, **kwargs: None)
    monkeypatch.setattr(models.hglib, "open", lambda repo_dir: contextlib.nullcontext())
    monkeypatch.setattr(
        models.repository,
        "get_ignore_revs",
        lambda hg, repo_dir: {nodes["newly_ignored"]},
    )

    db_names = ("backout", "scored", "newly_ignored", "backedout", "newly_backedout")

    def get_backedoutby(hg, revs):
        assert sorted(revs) == sorted(nodes[name].encode() for name in db_names)
        # Backouts which are not in the local repository yet are not known.
        return {
            rev.decode(): "9" * 40 if rev == nodes["newly_backedout"].encode() else ""
            for rev in revs
        }

    monkeypatch.setattr(models.repository, "get_backedoutby", get_backedoutby)

    def commit(name):
        return {
            "node": nodes[name],
            "bug_id": 1,
            "desc": name,
            "backsout": ["0" * 40] if name == "backout" else [],
            "ignored": name == "ignored",
            "backedoutby": "9" * 40 if name in ("backedout", "mined_backedout") else "",
        }

    # The commits already in the commits DB are not mined again.
    def get_commits_from_db(db_nodes):
        return {nodes[name]: commit(name) for name in db_names}

    def download_commits(repo_dir, revs, **kwargs):
        assert revs == [
            nodes["ignored"].encode(),
            nodes["merge"].encode(),
            nodes["mined_backedout"].encode(),
        ]
        # Merge commits are not mined.
        return (commit("ignored"), commit("mined_backedout"))

    monkeypatch.setattr(models.repository, "get_commits_from_db", get_commits_from_db)
    monkeypatch.setattr(models.repository, "download_commits", download_commits)
    model = MockRegressorModel()
    monkeypatch.setattr(models.MODEL_CACHE, "get", lambda model_name: model)

    assert models.classify_commits("regressor", "try", list(nodes.values())) == "OK"

    assert [commit["node"] for commit in model.classified] == [
        nodes["scored"],
        nodes["backedout"],
        nodes["newly_backedout"],
        nodes["mined_backedout"],
    ]
    for name, reason in (
        ("backout", "backout"),
        ("ignored", "ignored"),
        ("newly_ignored", "ignored"),
    ):
        result = _get_result(nodes[name], "try")
        assert result["ignored_reason"] == reason
        assert result["prob"] is None
        assert result["risk_band"] is None
    assert _get_result(nodes["scored"], "try")["prob"] == [0.25, 0.75]
    assert _get_result(nodes["scored"], "try")["ignored_reason"] is None
    # Backed-out commits are scored anyway.
    for name in ("backedout", "newly_backedout", "mined_backedout"):
        result = _get_result(nodes[name], "try")
        assert result["ignored_reason"] == "backedout"
        assert result["prob"] == [0.25, 0.75]
        assert result["risk_band"] == "h"
    assert _get_result(nodes["merge"], "try") == {"available": False}


def _bug_job_key(bug_id: int) -> str:
    return str(JobInfo(models.get_bug_commits, bug_id))


def _get_bug_result(bug_id: int):
    job = JobInfo(models.get_bug_commits, bug_id)
    result = models.redis.get(job.result_key)
    assert result is not None
    return orjson.loads(result)


def _commit_prediction(node: str, risk_band: str | None, ignored_reason: str | None):
    return {
        **PREDICTION,
        "node": node,
        "risk_band": risk_band,
        "ignored_reason": ignored_reason,
    }


def test_bugs_endpoint(client, jobs, add_result) -> None:
    endpoint = "/regressor/predict/bugs/autoland"

    assert client.post(endpoint, json={"bugs": [1]}).status_code == 401

    response = client.post(
        "/component/predict/bugs/autoland",
        json={"bugs": [1]},
        headers={API_TOKEN: "test"},
    )
    assert response.status_code == 404

    # The commits of bugs are only found on autoland.
    response = client.post(
        "/regressor/predict/bugs/try", json={"bugs": [1]}, headers={API_TOKEN: "test"}
    )
    assert response.status_code == 404

    bodies: tuple[object, ...] = (
        {},
        {"bugs": []},
        {"bugs": ["1"]},
        {"bugs": [0]},
        {"bugs": [1] * 21},
        [1],
    )
    for body in bodies:
        response = client.post(endpoint, json=body, headers={API_TOKEN: "test"})
        assert response.status_code == 400, body

    response = client.post(
        endpoint, json={"bugs": [1, 2, 3, 1]}, headers={API_TOKEN: "test"}
    )
    assert response.status_code == 202
    assert _response_json(response) == {
        "bugs": {"1": {"ready": False}, "2": {"ready": False}, "3": {"ready": False}}
    }
    # The bugs are looked up by a single job.
    assert len(jobs) == 1
    assert next(iter(jobs.values()))[:3] == [_bug_job_key(rev) for rev in (1, 2, 3)]

    # The bugs are not queued again while they are being processed.
    response = client.post(
        endpoint, json={"bugs": [1, 2, 3]}, headers={API_TOKEN: "test"}
    )
    assert response.status_code == 202
    assert len(jobs) == 1

    rev3, rev4 = "c" * 40, "d" * 40
    add_result(
        _bug_job_key(1), {"commits": [REV1, REV2, rev3, rev4], "backedout": [REV2]}
    )
    add_result(_bug_job_key(2), {"commits": [REV1], "backedout": []})
    add_result(_bug_job_key(3), {"commits": [], "backedout": []})

    response = client.post(
        endpoint, json={"bugs": [1, 2, 3]}, headers={API_TOKEN: "test"}
    )
    assert response.status_code == 202
    assert _response_json(response) == {
        "bugs": {
            "1": {"ready": False},
            "2": {"ready": False},
            "3": {"commits": {}, "risk_band": None},
        }
    }
    # The commits of the bug are queued.
    assert len(jobs) == 2
    assert list(jobs.values())[1][:4] == [
        _job_key(rev) for rev in (REV1, REV2, rev3, rev4)
    ]

    add_result(_job_key(REV1), _commit_prediction(REV1, "a", None))
    add_result(_job_key(REV2), _commit_prediction(REV2, "v", "backedout"))
    add_result(_job_key(rev3), {"available": False})

    response = client.post(endpoint, json={"bugs": [1]}, headers={API_TOKEN: "test"})
    assert response.status_code == 202
    assert _response_json(response) == {"bugs": {"1": {"ready": False}}}

    add_result(_job_key(rev4), _commit_prediction(rev4, "h", None))

    response = client.post(endpoint, json={"bugs": [1, 2]}, headers={API_TOKEN: "test"})
    assert response.status_code == 200
    assert _response_json(response) == {
        "bugs": {
            "1": {
                "commits": {
                    REV1: {"risk_band": "a", "ignored_reason": None},
                    REV2: {"risk_band": "v", "ignored_reason": "backedout"},
                    rev3: {"available": False},
                    rev4: {"risk_band": "h", "ignored_reason": None},
                },
                # The backed-out commit doesn't count.
                "risk_band": "h",
            },
            "2": {
                "commits": {REV1: {"risk_band": "a", "ignored_reason": None}},
                "risk_band": "a",
            },
        }
    }


def test_bugs_endpoint_not_scored(client, jobs, add_result) -> None:
    add_result(_bug_job_key(1), {"commits": [REV1, REV2], "backedout": [REV2]})
    add_result(_job_key(REV1), _commit_prediction(REV1, None, "backout"))
    add_result(_job_key(REV2), _commit_prediction(REV2, "l", "backedout"))

    response = client.post(
        "/regressor/predict/bugs/autoland",
        json={"bugs": [1]},
        headers={API_TOKEN: "test"},
    )
    assert response.status_code == 200
    assert _response_json(response)["bugs"]["1"]["risk_band"] is None


def test_bugs_endpoint_backed_out_after_classification(client, add_result) -> None:
    add_result(_bug_job_key(1), {"commits": [REV1, REV2], "backedout": [REV2]})
    add_result(_job_key(REV1), _commit_prediction(REV1, "a", None))
    # Classified before it was backed out.
    add_result(_job_key(REV2), _commit_prediction(REV2, "v", None))

    response = client.post(
        "/regressor/predict/bugs/autoland",
        json={"bugs": [1]},
        headers={API_TOKEN: "test"},
    )
    assert response.status_code == 200
    assert _response_json(response)["bugs"]["1"] == {
        "commits": {
            REV1: {"risk_band": "a", "ignored_reason": None},
            REV2: {"risk_band": "v", "ignored_reason": "backedout"},
        },
        "risk_band": "a",
    }


def test_get_bug_commits(monkeypatch) -> None:
    calls = []

    def refresh_commits():
        calls.append("refresh")
        return True

    monkeypatch.setattr("bugbug_http.boot.refresh_commits", refresh_commits)
    monkeypatch.setattr(models.hglib, "open", lambda repo_dir: contextlib.nullcontext())

    nodes = ["1" * 40, "2" * 40, "3" * 40, "4" * 40]

    def get_bug_nodes_in_db(bug_ids):
        # The latest commits are added to the commits DB first.
        assert calls == ["refresh"]
        return {
            bug_id: {1: nodes[:3], 2: [nodes[2], nodes[3]]}.get(bug_id, [])
            for bug_id in bug_ids
        }

    def get_commits_from_db(db_nodes):
        assert db_nodes == nodes
        # The second one was backed out when it was mined, by a backout which is too far from it
        # to be found in the repository.
        return {
            node: {"node": node, "backedoutby": "9" * 40 if node == nodes[1] else ""}
            for node in db_nodes
        }

    def get_backedoutby(hg, revs):
        assert revs == [node.encode() for node in nodes]
        # The third one was backed out after it was mined.
        return {
            rev.decode(): "8" * 40 if rev == nodes[2].encode() else "" for rev in revs
        }

    monkeypatch.setattr(models.repository, "get_bug_nodes_in_db", get_bug_nodes_in_db)
    monkeypatch.setattr(models.repository, "get_commits_from_db", get_commits_from_db)
    monkeypatch.setattr(models.repository, "get_backedoutby", get_backedoutby)

    assert models.get_bug_commits([1, 2, 3, 1]) == "OK"

    assert _get_bug_result(1) == {"commits": nodes[:3], "backedout": nodes[1:3]}
    assert _get_bug_result(2) == {
        "commits": [nodes[2], nodes[3]],
        "backedout": [nodes[2]],
    }
    assert _get_bug_result(3) == {"commits": [], "backedout": []}
    job = JobInfo(models.get_bug_commits, 1)
    expirations = models.redis.expirations  # type: ignore[attr-defined]
    assert expirations[job.result_key] == models.BUG_COMMITS_EXPIRATION_TTL


def test_get_bug_commits_refresh_failure(monkeypatch) -> None:
    # The commits of the bugs which are not in the DB yet would be missing.
    monkeypatch.setattr("bugbug_http.boot.refresh_commits", lambda: False)

    def get_bug_nodes_in_db(bug_ids):
        raise AssertionError("The commits DB is not up to date")

    monkeypatch.setattr(models.repository, "get_bug_nodes_in_db", get_bug_nodes_in_db)

    assert models.get_bug_commits([1]) == "NOK"

    # Without a result, the bug is looked up again on the next request.
    job = JobInfo(models.get_bug_commits, 1)
    assert models.redis.get(job.result_key) is None
