# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os
from datetime import timedelta
from functools import lru_cache
from typing import Collection, Tuple

import orjson
import requests
import zstandard
from redis import Redis

from bugbug import bugzilla, repository
from bugbug.model import Model
from bugbug.models import load_model
from bugbug.utils import get_hgmo_stack
from bugbug_http.readthrough_cache import ReadthroughTTLCache

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()

MODELS_NAMES = [
    "defectenhancementtask",
    "component",
    "regression",
    "stepstoreproduce",
    "spambug",
    "testlabelselect",
    "testgroupselect",
]

DEFAULT_EXPIRATION_TTL = 7 * 24 * 3600  # A week
redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost/0"))

MODEL_CACHE: ReadthroughTTLCache[str, Model] = ReadthroughTTLCache(
    timedelta(hours=1), load_model
)
MODEL_CACHE.start_ttl_thread()

cctx = zstandard.ZstdCompressor(level=10)


def setkey(key: str, value: bytes, compress: bool = False) -> None:
    LOGGER.debug(f"Storing data at {key}: {value!r}")
    if compress:
        value = cctx.compress(value)
    redis.set(key, value)
    redis.expire(key, DEFAULT_EXPIRATION_TTL)


def classify_bug(model_name: str, bug_ids: Collection[int], bugzilla_token: str) -> str:
    from bugbug_http.app import JobInfo

    # This should be called in a process worker so it should be safe to set
    # the token here
    bug_ids_set = set(map(int, bug_ids))
    bugzilla.set_token(bugzilla_token)
    bugs = bugzilla.get(bug_ids)

    missing_bugs = bug_ids_set.difference(bugs.keys())

    for bug_id in missing_bugs:
        job = JobInfo(classify_bug, model_name, bug_id)

        # TODO: Find a better error format
        setkey(job.result_key, orjson.dumps({"available": False}))

    if not bugs:
        return "NOK"

    model = MODEL_CACHE.get(model_name)

    if not model:
        LOGGER.info("Missing model %r, aborting" % model_name)
        return "NOK"

    model_extra_data = model.get_extra_data()

    # TODO: Classify could choke on a single bug which could make the whole
    # job to fails. What should we do here?
    probs = model.classify(list(bugs.values()), True)
    indexes = probs.argmax(axis=-1)
    suggestions = model.le.inverse_transform(indexes)

    probs_list = probs.tolist()
    indexes_list = indexes.tolist()
    suggestions_list = suggestions.tolist()

    for i, bug_id in enumerate(bugs.keys()):
        data = {
            "prob": probs_list[i],
            "index": indexes_list[i],
            "class": suggestions_list[i],
            "extra_data": model_extra_data,
        }

        job = JobInfo(classify_bug, model_name, bug_id)
        setkey(job.result_key, orjson.dumps(data), compress=True)

        # Save the bug last change
        setkey(job.change_time_key, bugs[bug_id]["last_change_time"].encode())

    return "OK"


@lru_cache(maxsize=None)
def get_known_tasks() -> Tuple[str, ...]:
    with open("known_tasks", "r") as f:
        return tuple(line.strip() for line in f)


def schedule_tests(branch: str, rev: str) -> str:
    from bugbug_http.app import JobInfo
    from bugbug_http import REPO_DIR

    job = JobInfo(schedule_tests, branch, rev)
    LOGGER.info(f"Processing {job}...")

    # Pull the revision to the local repository
    LOGGER.info("Pulling commits from the remote repository...")
    repository.pull(REPO_DIR, branch, rev)

    # Load the full stack of patches leading to that revision
    LOGGER.info("Loading commits to analyze using automationrelevance...")
    try:
        revs = get_hgmo_stack(branch, rev)
    except requests.exceptions.RequestException:
        LOGGER.warning(f"Push not found for {branch} @ {rev}!")
        return "NOK"

    test_selection_threshold = float(
        os.environ.get("TEST_SELECTION_CONFIDENCE_THRESHOLD", 0.5)
    )

    # Analyze patches.
    commits = repository.download_commits(
        REPO_DIR, revs=revs, save=False, use_single_process=True, include_no_bug=True
    )

    if len(commits) > 0:
        testlabelselect_model = MODEL_CACHE.get("testlabelselect")
        testgroupselect_model = MODEL_CACHE.get("testgroupselect")

        tasks = testlabelselect_model.select_tests(commits, test_selection_threshold)

        reduced = testlabelselect_model.reduce(
            set(t for t, c in tasks.items() if c >= 0.8), 1.0
        )

        reduced_higher = testlabelselect_model.reduce(
            set(t for t, c in tasks.items() if c >= 0.9), 1.0
        )

        groups = testgroupselect_model.select_tests(commits, test_selection_threshold)

        config_groups = testgroupselect_model.select_configs(groups.keys(), 1.0)
    else:
        tasks = {}
        reduced = {}
        groups = {}
        config_groups = {}

    data = {
        "tasks": tasks,
        "groups": groups,
        "config_groups": config_groups,
        "reduced_tasks": {t: c for t, c in tasks.items() if t in reduced},
        "reduced_tasks_higher": {t: c for t, c in tasks.items() if t in reduced_higher},
        "known_tasks": get_known_tasks(),
    }
    setkey(job.result_key, orjson.dumps(data), compress=True)

    return "OK"
