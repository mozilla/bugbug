# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import logging
from collections import defaultdict
from datetime import datetime

import pytest
from rq.exceptions import NoSuchJobError

from bugbug_http import app


@pytest.fixture
def client():
    yield app.application.test_client()


@pytest.fixture
def jobs():
    """Shared jobs object that can be used by tests and fixtures.

    Of the form:

        {<job id>: [<labels>]}

    where label is the equivalent of `str(JobInfo(func, *args))`.
    """
    return defaultdict(list)


@pytest.fixture(autouse=True)
def patch_resources(monkeypatch, jobs):
    """Patch redis and rq with mock replacements."""

    class RedisMock:
        """Mock class to mimic a Redis database."""

        def __init__(self):
            self.data = {}

        def set(self, k, v):
            # keep track of job ids for testing purposes
            if k.startswith("bugbug:job_id"):
                key = k.split(":", 2)[-1]
                jobs[v].append(key)

            self.data[k] = v.encode("utf-8")

        def mset(self, d):
            for k, v in d.items():
                self.set(k, v)

        def get(self, k):
            return self.data.get(k)

        def exists(self, k):
            return k in self.data

        def delete(self, k):
            if self.exists(k):
                del self.data[k]

        def ping(self):
            pass

    class QueueMock:
        """Mock class to mimic rq.Queue."""

        def __init__(self, *args, **kwargs):
            pass

        def enqueue(self, func, *args, job_id=None):
            pass

    class JobMock:
        """Mock class to mimic rq.job.Job."""

        def __init__(self, job_id):
            self.job_id = job_id
            self.enqueued_at = datetime.today()
            self.timeout = 10

        @staticmethod
        def fetch(job_id, **kwargs):
            if job_id in jobs:
                return JobMock(job_id)

            raise NoSuchJobError

        def get_status(self):
            if self.job_id not in jobs:
                raise NoSuchJobError

            result_key = f"bugbug:job_result:{jobs[self.job_id][0]}"
            if result_key in app.redis_conn.data:
                return "finished"
            return "started"

        def cancel(self):
            if self.job_id in jobs:
                del jobs[self.job_id]

        def cleanup(self):
            pass

    app.LOGGER.setLevel(logging.DEBUG)
    monkeypatch.setattr(app, "redis_conn", RedisMock())
    monkeypatch.setattr(app, "q", QueueMock())
    monkeypatch.setattr(app, "Job", JobMock)


@pytest.fixture
def add_result():
    """Fixture that can be called to simulate a worker finishing a job."""

    def inner(key, data, change_time=None):
        result_key = f"bugbug:job_result:{key}"
        app.redis_conn.set(result_key, json.dumps(data))

    return inner


@pytest.fixture
def add_change_time():
    def inner(key, change_time):
        change_time_key = f"bugbug:change_time:{key}"
        app.redis_conn.set(change_time_key, change_time)

    return inner
