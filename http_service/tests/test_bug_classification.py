# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import gzip
import time
from types import SimpleNamespace

import numpy as np
import orjson

from bugbug_http import app, models
from bugbug_http.app import API_TOKEN, JobInfo


def retrieve_compressed_reponse(response):
    # Response is of type "<class 'flask.wrappers.Response'>" -  Flask Client's  Response
    # Not applicable for "<class 'requests.models.Response'> "
    if response.headers["Content-Encoding"] == "gzip":
        return orjson.loads(gzip.decompress(response.data))
    return response.json


def test_model_predict_id(client, jobs, add_result, responses):
    bug_id = "123456"
    result = {
        "class": "Core::Layout",
        "extra_data": {"index": 0, "prob": [0.0032219779677689075]},
    }

    responses.add(
        responses.GET,
        f"https://bugzilla.mozilla.org/rest/bug?id={bug_id}&include_fields=last_change_time&include_fields=id",
        status=200,
        json={
            "bugs": [{"id": bug_id, "last_change_time": time.time()}],
        },
    )

    def do_request():
        return client.get(
            "/component/predict/123456",
            headers={API_TOKEN: "test"},
        )

    rv = do_request()
    assert rv.status_code == 202
    assert retrieve_compressed_reponse(rv) == {"ready": False}

    # request still not ready
    rv = do_request()
    assert rv.status_code == 202
    assert retrieve_compressed_reponse(rv) == {"ready": False}
    assert len(jobs) == 1

    # now it's ready
    keys = next(iter(jobs.values()))
    add_result(keys[0], result)

    rv = do_request()
    assert rv.status_code == 200
    assert retrieve_compressed_reponse(rv) == result


def test_model_predict_id_github(client, jobs, add_result, responses):
    issue_id = "12345"
    result = {
        "prob": [0.11845558881759644, 0.8815444111824036],
        "index": 1,
        "class": 1,
        "extra_data": {},
    }

    responses.add(
        responses.GET,
        f"https://api.github.com/repos/webcompat/web-bugs/issues/{issue_id}",
        status=200,
        json={"number": issue_id, "updated_at": time.time()},
    )

    def do_request():
        return client.get(
            "/needsdiagnosis/predict/github/webcompat/web-bugs/12345",
            headers={API_TOKEN: "test"},
        )

    rv = do_request()
    assert rv.status_code == 202
    assert retrieve_compressed_reponse(rv) == {"ready": False}

    # request still not ready
    rv = do_request()
    assert rv.status_code == 202
    assert retrieve_compressed_reponse(rv) == {"ready": False}
    assert len(jobs) == 1

    # now it's ready
    keys = next(iter(jobs.values()))
    add_result(keys[0], result)

    rv = do_request()
    assert rv.status_code == 200
    assert retrieve_compressed_reponse(rv) == result


def test_model_predict_batch(client, jobs, add_result, add_change_time, responses):
    bug_ids = [123, 456]
    result = {
        "class": "Core::Layout",
        "extra_data": {"index": 0, "prob": [0.0032219779677689075]},
    }
    change_time = str(time.time())

    responses.add(
        responses.GET,
        "https://bugzilla.mozilla.org/rest/bug?id=123,456&include_fields=id&include_fields=last_change_time",
        status=200,
        json={
            "bugs": [
                {"id": bug_id, "last_change_time": change_time} for bug_id in bug_ids
            ],
        },
    )

    def do_request():
        return client.post(
            "/component/predict/batch",
            json={"bugs": bug_ids},
            headers={API_TOKEN: "test"},
        )

    rv = do_request()
    assert rv.status_code == 202
    assert retrieve_compressed_reponse(rv) == {
        "bugs": {str(bug_id): {"ready": False} for bug_id in bug_ids}
    }
    assert len(jobs) == 1

    # one of the bugs is ready
    keys = next(iter(jobs.values()))
    for key in keys:
        # Need to set change times in redis or results will be invalidated.
        add_change_time(key, change_time)

    add_result(keys[0], result, change_time=change_time)

    rv = do_request()
    assert rv.status_code == 202
    assert retrieve_compressed_reponse(rv) == {
        "bugs": {str(bug_ids[0]): result, str(bug_ids[1]): {"ready": False}}
    }

    # now they're both ready
    add_result(keys[1], result, change_time=change_time)

    rv = do_request()
    assert rv.status_code == 200
    assert retrieve_compressed_reponse(rv) == {
        "bugs": {str(bug_id): result for bug_id in bug_ids}
    }


def test_model_predict_batch_broken_site_reports(client, jobs, add_result):
    reports = [
        {
            "uuid": "954dbc23-91e6-4d6f-a10a-405f46663e31",
            "title": "https://example.com",
            "body": "Loads blank page.",
        },
        {
            "uuid": "af7e27b5-3ce3-46e1-8294-5baea36782cc",
            "title": "https://test.com",
            "body": "Will not load",
        },
    ]
    result = {
        "prob": [0.11845558881759644, 0.8815444111824036],
        "index": 1,
        "class": 1,
        "extra_data": {},
    }

    def do_request():
        return client.post(
            "/invalidcompatibilityreport/predict/broken_site_report/batch",
            json={"reports": reports},
            headers={API_TOKEN: "test"},
        )

    rv = do_request()
    assert rv.status_code == 202
    assert retrieve_compressed_reponse(rv) == {
        "reports": {report["uuid"]: {"ready": False} for report in reports}
    }
    assert len(jobs) == 1

    # set one of the reports as ready
    keys = next(iter(jobs.values()))
    add_result(keys[0], result)

    rv = do_request()
    assert rv.status_code == 202
    assert retrieve_compressed_reponse(rv) == {
        "reports": {reports[0]["uuid"]: result, reports[1]["uuid"]: {"ready": False}}
    }

    # set both reports as ready
    add_result(keys[1], result)

    rv = do_request()
    assert rv.status_code == 200
    assert retrieve_compressed_reponse(rv) == {
        "reports": {report["uuid"]: result for report in reports}
    }


def test_for_missing_bugs(client, responses):
    existed_bug_ids = [1602463, 1619699]
    missing_bug_ids = [1598744, 1615281, 1566486]
    all_bug_ids = [*existed_bug_ids, *missing_bug_ids]

    change_time = str(time.time())

    responses.add(
        responses.GET,
        "https://bugzilla.mozilla.org/rest/bug?id=1566486,1598744,1602463,1615281,1619699&include_fields=id&include_fields=last_change_time",
        status=200,
        json={
            "bugs": [
                {"id": bug_id, "last_change_time": change_time}
                for bug_id in existed_bug_ids
            ],
        },
    )

    rv = client.post(
        "/component/predict/batch",
        json={"bugs": all_bug_ids},
        headers={API_TOKEN: "test"},
    )
    assert rv.status_code == 202
    bugs = retrieve_compressed_reponse(rv)["bugs"]
    assert bugs.keys() == set(map(str, all_bug_ids)), (
        "All the queried bug IDs must be returned"
    )


def test_empty_batch(client):
    """Start with a blank database."""

    rv = client.post(
        "/component/predict/batch",
        json={"bugs": []},
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 400
    assert rv.json == {"errors": {"bugs": ["min length is 1"]}}


def test_non_int_batch(client):
    """Start with a blank database."""

    bugs = ["1", "2", "3"]

    rv = client.post(
        "/component/predict/batch",
        json={"bugs": bugs},
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 400
    assert rv.json == {
        "errors": {
            "bugs": [
                {
                    "0": ["must be of integer type"],
                    "1": ["must be of integer type"],
                    "2": ["must be of integer type"],
                }
            ]
        }
    }


def test_unknown_model(client):
    """Start with  a blank database,"""
    bugs = [1, 2, 3]
    unknown_model = "unknown_model"

    rv = client.post(
        f"/{unknown_model}/predict/batch",
        json={"bugs": bugs},
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 404
    assert rv.json == {"error": f"Model {unknown_model} doesn't exist"}


def test_no_api_key(client):
    """Start with an empty database,"""

    bugs = [1, 2, 3]

    rv = client.post(
        "/component/predict/batch",
        json={"bugs": bugs},
        headers={},
    )

    assert rv.status_code == 401
    assert rv.json == {"message": "Error, missing X-API-KEY"}


def test_comment_prediction(client, jobs, add_result, add_change_time, monkeypatch):
    comment_id = 123
    result = {"class": "spam", "extra_data": {}, "index": 1, "prob": [0, 1]}
    monkeypatch.setattr(app, "is_comment_model", lambda model_name: True)
    monkeypatch.setattr(
        app,
        "get_comments_last_change_time",
        lambda comment_ids: {comment_id: "2026-09-21T00:00:00Z"},
    )

    def do_request():
        return client.get(
            f"/spamcomment/predict/comment/{comment_id}",
            headers={API_TOKEN: "test"},
        )

    response = do_request()
    assert response.status_code == 202
    assert retrieve_compressed_reponse(response) == {"ready": False}

    key = next(iter(jobs.values()))[0]
    add_change_time(key, "2026-09-21T00:00:00Z")
    add_result(key, result)

    response = do_request()
    assert response.status_code == 200
    assert retrieve_compressed_reponse(response) == result


def test_batch_comment_prediction(client, jobs, monkeypatch):
    comment_ids = [123, 456]
    monkeypatch.setattr(app, "is_comment_model", lambda model_name: True)
    monkeypatch.setattr(
        app,
        "get_comments_last_change_time",
        lambda requested_ids: {
            comment_id: "2026-09-21T00:00:00Z" for comment_id in requested_ids
        },
    )

    response = client.post(
        "/spamcomment/predict/comment/batch",
        json={"comments": comment_ids},
        headers={API_TOKEN: "test"},
    )

    assert response.status_code == 202
    assert retrieve_compressed_reponse(response) == {
        "comments": {str(comment_id): {"ready": False} for comment_id in comment_ids}
    }
    job_keys = next(iter(jobs.values()))
    assert job_keys[:2] == [
        f"classify_comment:spamcomment_{comment_id}" for comment_id in comment_ids
    ]
    assert len(job_keys) == 3


def test_inaccessible_comment_clears_cached_prediction(client, add_result, monkeypatch):
    comment_id = 123
    monkeypatch.setattr(app, "is_comment_model", lambda model_name: True)
    monkeypatch.setattr(app, "get_comments_last_change_time", lambda comment_ids: {})
    job = JobInfo(models.classify_comment, "spamcomment", comment_id)
    add_result(job, {"class": "spam"})

    response = client.get(
        f"/spamcomment/predict/comment/{comment_id}",
        headers={API_TOKEN: "test"},
    )

    assert response.status_code == 200
    assert retrieve_compressed_reponse(response) == {"available": False}
    assert app.redis_conn.get(job.result_key) is None


def test_classify_comment_worker(monkeypatch):
    bug = {"id": 10, "last_change_time": "2026-09-21T00:00:00Z"}
    comment = {"id": 123, "bug_id": 10, "count": 1}
    classified_items = []

    class FakeModel:
        le = SimpleNamespace(
            inverse_transform=lambda indexes: np.array(["spam"] * len(indexes))
        )

        def classify(self, items, probabilities):
            classified_items.extend(items)
            return np.array([[0.1, 0.9]])

        def get_extra_data(self):
            return {}

    monkeypatch.setattr(
        models.bugzilla,
        "get_comments",
        lambda comment_ids: {123: (bug, comment)},
    )
    monkeypatch.setattr(models.MODEL_CACHE, "get", lambda model_name: FakeModel())

    assert models.classify_comment("spamcomment", [123, 999], "token") == "OK"
    assert classified_items == [(bug, comment)]
    assert app.get_result(JobInfo(models.classify_comment, "spamcomment", 123)) == {
        "prob": [0.1, 0.9],
        "index": 1,
        "class": "spam",
        "extra_data": {},
    }
    assert app.get_result(JobInfo(models.classify_comment, "spamcomment", 999)) == {
        "available": False
    }
