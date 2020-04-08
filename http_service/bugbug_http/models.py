# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os
from datetime import timedelta

import orjson
import requests
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


def setkey(key, value, expiration=DEFAULT_EXPIRATION_TTL):
    LOGGER.debug(f"Storing data at {key}: {value}")
    redis.set(key, value)

    if expiration > 0:
        redis.expire(key, expiration)


def classify_bug(model_name, bug_ids, bugzilla_token):
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
        encoded_data = orjson.dumps({"available": False})
        setkey(job.result_key, encoded_data)

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
        setkey(job.result_key, orjson.dumps(data))

        # Save the bug last change
        setkey(job.change_time_key, bugs[bug_id]["last_change_time"], expiration=0)

    return "OK"


def schedule_tests(branch, rev):
    from bugbug_http.app import JobInfo
    from bugbug_http import REPO_DIR

    job = JobInfo(schedule_tests, branch, rev)
    LOGGER.debug(f"Processing {job}")

    # Load the full stack of patches leading to that revision
    try:
        stack = get_hgmo_stack(branch, rev)
    except requests.exceptions.RequestException:
        LOGGER.warning(f"Push not found for {branch} @ {rev}!")
        return "NOK"

    # Apply the stack on the local repository
    try:
        revs = repository.apply_stack(REPO_DIR, stack, branch)
    except Exception as e:
        LOGGER.warning(f"Failed to apply stack {branch} @ {rev}: {e}")
        return "NOK"

    test_selection_threshold = float(
        os.environ.get("TEST_SELECTION_CONFIDENCE_THRESHOLD", 0.3)
    )

    # Analyze patches.
    commits = repository.download_commits(
        REPO_DIR, revs=revs, save=False, use_single_process=True
    )

    data = {
        "tasks": MODEL_CACHE.get("testlabelselect").select_tests(
            commits, test_selection_threshold
        ),
        "groups": MODEL_CACHE.get("testgroupselect").select_tests(
            commits, test_selection_threshold
        ),
    }
    setkey(job.result_key, orjson.dumps(data))

    return "OK"
