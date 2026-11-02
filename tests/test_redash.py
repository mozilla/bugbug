# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json

import pytest
import responses

from bugbug import redash

BASE = "https://redash.example"


def _add_job(path: str, job_id: str, result_id: int, rows: list[dict]) -> None:
    responses.add(
        responses.POST,
        f"{BASE}{path}",
        json={"job": {"id": job_id, "status": 1, "query_result_id": None, "error": ""}},
    )
    responses.add(
        responses.GET,
        f"{BASE}/api/jobs/{job_id}",
        json={"job": {"id": job_id, "status": 2, "query_result_id": None, "error": ""}},
    )
    responses.add(
        responses.GET,
        f"{BASE}/api/jobs/{job_id}",
        json={
            "job": {
                "id": job_id,
                "status": 3,
                "query_result_id": result_id,
                "error": "",
            }
        },
    )
    responses.add(
        responses.GET,
        f"{BASE}/api/query_results/{result_id}.json",
        json={"query_result": {"data": {"rows": rows, "columns": []}}},
    )


@responses.activate
def test_execute_polls_the_job() -> None:
    _add_job("/api/query_results", "j1", 7, [{"id": 1}])
    client = redash.RedashClient("key", base_url=BASE, poll_interval=0)
    assert client.execute(89, "SELECT 1") == [{"id": 1}]
    assert responses.calls[0].request.headers["Authorization"] == "Key key"
    assert json.loads(responses.calls[0].request.body) == {
        "data_source_id": 89,
        "query": "SELECT 1",
        "max_age": 0,
    }


@responses.activate
def test_query_runs_a_saved_query_with_parameters() -> None:
    _add_job("/api/queries/119580/results", "j2", 8, [{"first_push_id": 3}])
    client = redash.RedashClient("key", base_url=BASE, poll_interval=0)
    assert client.query(119580, {"repository_id": 4}) == [{"first_push_id": 3}]
    assert json.loads(responses.calls[0].request.body) == {
        "parameters": {"repository_id": 4},
        "max_age": 1800,
    }


@responses.activate
def test_cached_result_is_returned_without_a_job() -> None:
    responses.add(
        responses.POST,
        f"{BASE}/api/queries/5/results",
        json={"query_result": {"data": {"rows": [{"n": 2}], "columns": []}}},
    )
    client = redash.RedashClient("key", base_url=BASE, poll_interval=0)
    assert client.query(5, {}) == [{"n": 2}]
    assert len(responses.calls) == 1


@responses.activate
def test_failed_job_raises() -> None:
    responses.add(
        responses.POST,
        f"{BASE}/api/query_results",
        json={
            "job": {"id": "j3", "status": 4, "query_result_id": None, "error": "boom"}
        },
    )
    client = redash.RedashClient("key", base_url=BASE, poll_interval=0)
    with pytest.raises(RuntimeError, match="boom"):
        client.execute(89, "SELECT 1")


@responses.activate
def test_query_redash_uses_the_configured_key(monkeypatch) -> None:
    monkeypatch.setenv("REDASH_API_KEY", "env-key")
    responses.add(
        responses.POST,
        f"{redash.REDASH_URL}/api/queries/5/results",
        json={"query_result": {"data": {"rows": [{"n": 1}], "columns": []}}},
    )
    assert redash.query_redash(5, {"a": 1}) == [{"n": 1}]
    assert responses.calls[0].request.headers["Authorization"] == "Key env-key"
