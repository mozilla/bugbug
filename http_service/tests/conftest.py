# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime

import hglib
import numpy as np
import pytest
import responses
from rq.exceptions import NoSuchJobError

import bugbug.models
import bugbug.models.testselect
import bugbug_http
import bugbug_http.models
from bugbug import repository, test_scheduling
from bugbug_http import app

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def get_fixture_path():
    def _get_fixture_path(path):
        path = os.path.join(FIXTURES_DIR, path)
        assert os.path.exists(path), f"Missing fixture {path}"
        return path

    return _get_fixture_path


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
            self.expirations = {}

        def set(self, k, v):
            # keep track of job ids for testing purposes
            if k.startswith("bugbug:job_id"):
                key = k.split(":", 2)[-1]
                jobs[v].append(key)

            if not isinstance(v, bytes):
                v = v.encode("ascii")

            self.data[k] = v

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

        def expire(self, key, expiration):
            self.expirations[key] = expiration

    class QueueMock:
        """Mock class to mimic rq.Queue."""

        def __init__(self, *args, **kwargs):
            pass

        def enqueue(self, func, *args, job_id=None, ttl=None, failure_ttl=None):
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
    _redis = RedisMock()
    monkeypatch.setattr(app, "redis_conn", _redis)
    monkeypatch.setattr(bugbug_http.models, "redis", _redis)
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


@pytest.fixture
def mock_hgmo(get_fixture_path, mock_repo):
    """Mock HGMO API to get patches to apply"""

    def fake_raw_rev(request):
        *repo, _, revision = request.path_url[1:].split("/")
        repo = "-".join(repo)

        assert repo != "None", "Missing repo"
        assert revision != "None", "Missing revision"

        mock_path = get_fixture_path(f"hgmo_{repo}/{revision}.diff")
        with open(mock_path) as f:
            content = f.read()

        return (200, {"Content-Type": "text/plain"}, content)

    def fake_json_relevance(request):
        *repo, _, revision = request.path_url[1:].split("/")
        repo = "-".join(repo)

        assert repo != "None", "Missing repo"
        assert revision != "None", "Missing revision"

        mock_path = get_fixture_path(f"hgmo_{repo}/{revision}.json")
        with open(mock_path) as f:
            content = f.read()

        # Patch the hardcoded revisions using the remote repo
        with hglib.open(str(mock_repo[1])) as repo:
            for log in repo.log():
                desc = log.desc.decode("utf-8")
                node = log.node.decode("utf-8")
                content = content.replace(desc.replace(" ", "_").upper(), node)

        return (200, {"Content-Type": "application/json"}, content)

    responses.add_callback(
        responses.GET,
        re.compile(r"^https?://(hgmo|hg\.mozilla\.org)/[\w\-\/]+/raw-rev/(\w+)"),
        callback=fake_raw_rev,
    )
    responses.add_callback(
        responses.GET,
        re.compile(
            r"^https?://(hgmo|hg\.mozilla\.org)/[\w\-\/]+/json-automationrelevance/(\w+)"
        ),
        callback=fake_json_relevance,
    )


@pytest.fixture
def mock_repo(tmpdir, monkeypatch):
    """Create an empty mercurial repo"""
    local_dir = tmpdir / "local"
    remote_dir = tmpdir / "remote"

    # Setup the worker env to use that repo dir
    monkeypatch.setattr(bugbug_http, "REPO_DIR", str(local_dir))

    # Create the repo
    hglib.init(str(local_dir))

    (local_dir / ".hg-annotate-ignore-revs").write_text("", encoding="ascii")

    # Add several commits on a test file to create some history
    test_file = local_dir / "test.txt"
    with hglib.open(str(local_dir)) as repo:
        for i in range(4):
            test_file.write_text(f"Version {i}", encoding="utf-8")
            repo.add([str(test_file).encode("utf-8")])
            repo.commit(f"Base history {i}", user="bugbug")

    # Copy initialized repo as remote
    local_dir.copy(remote_dir)

    # Configure remote on local
    hgrc = local_dir / ".hg" / "hgrc"
    hgrc.write_text("\n".join(["[paths]", f"default = {remote_dir}"]), "utf-8")

    # Add extra commit on remote
    with hglib.open(str(remote_dir)) as repo:
        remote = remote_dir / "remote.txt"
        remote.write_text("New remote file !", encoding="utf-8")
        repo.add([str(remote).encode("utf-8")])
        repo.commit("Pulled from remote", user="bugbug")

    # Allow using the local code analysis server.
    responses.add_passthru("http://127.0.0.1")

    return local_dir, remote_dir


@pytest.fixture
def mock_data(tmp_path):
    os.mkdir(tmp_path / "data")
    os.chdir(tmp_path)


@pytest.fixture
def mock_component_taskcluster_artifact():
    responses.add(
        responses.HEAD,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        headers={"ETag": "100"},
    )

    responses.add(
        responses.GET,
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        status=200,
        json={},
    )

    repository.download_component_mapping()


@pytest.fixture
def mock_schedule_tests_classify(monkeypatch):
    # Initialize a mock past failures DB.
    for granularity in ("label", "group"):
        past_failures_data = test_scheduling.get_past_failures(granularity)
        past_failures_data["push_num"] = 1
        past_failures_data["all_runnables"] = [
            f"test-{granularity}1",
            f"test-{granularity}2",
        ]
        past_failures_data.close()

    def do_mock(labels_to_choose, groups_to_choose):
        # Add a mock test selection model.
        def classify(self, items, probabilities=False):
            assert probabilities
            results = []
            for item in items:
                runnable_name = item["test_job"]["name"]
                if self.granularity == "label":
                    if runnable_name in labels_to_choose:
                        results.append(
                            [
                                1 - labels_to_choose[runnable_name],
                                labels_to_choose[runnable_name],
                            ]
                        )
                    else:
                        results.append([0.9, 0.1])
                elif self.granularity == "group":
                    if runnable_name in groups_to_choose:
                        results.append(
                            [
                                1 - groups_to_choose[runnable_name],
                                groups_to_choose[runnable_name],
                            ]
                        )
                    else:
                        results.append([0.9, 0.1])
            return np.array(results)

        class MockModelCache:
            def get(self, model_name):
                if "group" in model_name:
                    return bugbug.models.testselect.TestGroupSelectModel()
                else:
                    return bugbug.models.testselect.TestLabelSelectModel()

        monkeypatch.setattr(bugbug_http.models, "MODEL_CACHE", MockModelCache())
        monkeypatch.setattr(
            bugbug.models.testselect.TestSelectModel, "classify", classify
        )

    return do_mock
