# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import gzip

import orjson

from bugbug_http.app import API_TOKEN


def retrieve_compressed_reponse(response):
    # Response is of type "<class 'flask.wrappers.Response'>" -  Flask Client's  Response
    # Not applicable for "<class 'requests.models.Response'> "
    if response.headers["Content-Encoding"] == "gzip":
        return orjson.loads(gzip.decompress(response.data))
    return response.json


def test_queue_job_valid(client, add_result, jobs):
    # schedule job
    rv = client.get(
        "/push/autoland/abcdef/schedules",
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 202
    assert rv.json == {"ready": False}

    # still not ready
    rv = client.get(
        "/push/autoland/abcdef/schedules",
        headers={API_TOKEN: "test"},
    )

    assert rv.status_code == 202
    assert rv.json == {"ready": False}

    # job done
    result = {
        "groups": ["foo/mochitest.ini", "bar/xpcshell.ini"],
        "tasks": ["test-linux/opt-mochitest-1"],
    }
    keys = next(iter(jobs.values()))
    add_result(keys[0], result)

    rv = client.get(
        "/push/autoland/abcdef/schedules",
        headers={API_TOKEN: "test"},
    )
    assert rv.status_code == 200
    assert retrieve_compressed_reponse(rv) == result


def test_no_api_key(client):
    rv = client.get("/push/autoland/foobar/schedules")

    assert rv.status_code == 401
    assert rv.json == {"message": "Error, missing X-API-KEY"}
