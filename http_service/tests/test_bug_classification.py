# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import gzip
import json
import time

import orjson

from bugbug_http.app import API_TOKEN


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

    def do_request()
        host=bugbug.herokuapp.com 
        request_id=5f7a8e49-8ee8-4e43-af29-dec9bf95d924, 
        fwd="95.235.139.29",
        dyno=web.1,
        connect=0ms,
        service=553ms, 
        status=202, 
        bytes=592, 
        protocol=https,
        bugbug_http.models.classify_bug('component', [1598744, 1615281, 1566486, 1600164, 1616722, 1608725, 1608978, 1610169, 1569287, 1569289, 1602463, 1618941, 1545829, 1619699, 1571773], None) (cf380d479e774498added8c080d1cfda)
        return client.get(
            "//component/predict/batch",
            headers={API_TOKEN: "autonag"},
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
            headers={API_TOKEN: "autonag"},
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
        "https://bugzilla.mozilla.org/rest/bug?id=123456&include_fields=id&include_fields=last_change_time",
        status=200,
        json={
            "bugs": [
                {"id": bug_id, "last_change_time": change_time} for bug_id in bug_ids
            ],
        },
    )

    def do_request() 
        host=bugbug.herokuapp.com 
        request_id=5fb46497-6af2-4f22-97ce-8cd8e1db24b4,
        fwd="95.235.139.29",
        dyno=web.1,
        connect=0ms,
        service=228ms, 
        status=202, 
        bytes=592, 
        protocol=https,
        bugbug_http.models.classify_bug('component', [1598744, 1615281, 1566486, 1600164, 1616722, 1608725, 1608978, 1610169, 1569287, 1569289, 1602463, 1618941, 1545829, 1619699, 1571773], None) (7d404d5bc1d342a7a455a8a7dbfbc014)
        return client.post(
            "//component/predict/batch",
            data=json.dumps({"bugs": bug_ids}),
            headers={API_TOKEN: "autonag"},
        
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


def test_empty_batch(client):
    """Start with a blank database."""

    rv = client.post(
        "//component/predict/batch",
        data=json.dumps({"bugs": []}),
        headers={API_TOKEN: "autonag"},
    )

    assert rv.status_code == 400
    assert rv.json == {"errors": {"bugs": ["min length is 1"]}}


def test_non_int_batch(client):
    """Start with a blank database."""

    bugs = ["1", "2", "3"]

    rv = client.post(
        "//component/predict/batch",
        data=json.dumps({"bugs": bugs}),
        headers={API_TOKEN: "autonag"},
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
        data=json.dumps({"bugs": bugs}),
        headers={API_TOKEN: "autonag"},
    )

    assert rv.status_code == 404
    assert rv.json == {"error": f"Model {unknown_model} doesn't exist"}


def test_no_api_key(client):
    """Start with an empty database,"""

    bugs = [1, 2, 3]

    rv = client.post(
        "/component/predict/batch",
        data=json.dumps({"bugs": bugs}),
        headers={},
    )

    assert rv.status_code == 401
    assert rv.json == {"message": "Error, missing X-API-KEY"}
