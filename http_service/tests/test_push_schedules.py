# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import logging
import time

import pytest
from rq.exceptions import NoSuchJobError

from bugbug_http import app

# { <job id>: <result key> }
JOBS = {}


class RedisMock:
    """Mock class to mimic a Redis database."""

    def __init__(self):
        self.data = {}

    def set(self, k, v):
        self.data[k] = v.encode("utf-8")

    def mset(self, d):
        for k, v in d.items():
            self.set(k, v)

    def get(self, k):
        return self.data.get(k)

    def exists(self, k):
        return k in self.data

    def delete(self, k):
        del self.data[k]

    def ping(self):
        pass


class QueueMock:
    def __init__(self, *args, **kwargs):
        pass

    def enqueue(self, func, *args, job_id=None):
        job = app.JobInfo(func, *args)
        JOBS[job_id] = job.result_key


class JobMock:
    def __init__(self, job_id):
        self.job_id = job_id
        self.enqueued_at = time.time()
        self.timeout = 10

    @staticmethod
    def fetch(job_id, **kwargs):
        if job_id in JOBS:
            return JobMock(job_id)

        raise NoSuchJobError

    def get_status(self):
        if self.job_id not in JOBS:
            raise NoSuchJobError

        result_key = JOBS[self.job_id]
        if result_key in app.redis_conn.data:
            return "finished"
        return "started"

    def cancel(self):
        if self.job_id in JOBS:
            del JOBS[self.job_id]

    def cleanup(self):
        pass


@pytest.fixture(autouse=True)
def patch_resources(monkeypatch):
    global JOBS
    JOBS = {}

    app.LOGGER.setLevel(logging.DEBUG)
    monkeypatch.setattr(app, "redis_conn", RedisMock())
    monkeypatch.setattr(app, "q", QueueMock())
    monkeypatch.setattr(app, "Job", JobMock)


@pytest.fixture
def add_result():
    """Fixture that can be called to simulate a worker finishing a job."""

    def inner(job_id, data):
        result_key = JOBS.pop(job_id)
        app.redis_conn.set(result_key, json.dumps(data))

    return inner


def test_queue_job_valid(client, add_result):
    # schedule job
    rv = client.get("/push/autoland/abcdef/schedules", headers={app.API_TOKEN: "test"},)

    assert rv.status_code == 202
    assert rv.json == {"ready": False}

    # still not ready
    rv = client.get("/push/autoland/abcdef/schedules", headers={app.API_TOKEN: "test"},)

    assert rv.status_code == 202
    assert rv.json == {"ready": False}

    # job done
    result = {
        "groups": ["foo/mochitest.ini", "bar/xpcshell.ini"],
        "tasks": ["test-linux/opt-mochitest-1"],
    }
    job_id = list(JOBS.keys())[0]
    add_result(job_id, result)

    rv = client.get("/push/autoland/abcdef/schedules", headers={app.API_TOKEN: "test"},)
    assert rv.status_code == 200
    assert rv.json == result


def test_no_api_key(client):
    rv = client.get("/push/autoland/foobar/schedules")

    assert rv.status_code == 401
    assert rv.json == {"message": "Error, missing X-API-KEY"}
