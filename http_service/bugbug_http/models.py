# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import logging
import os
from datetime import datetime
from typing import Dict

import numpy as np
import requests
from dateutil.relativedelta import relativedelta
from redis import Redis

from bugbug import bugzilla, commit_features, repository, test_scheduling
from bugbug.model import Model
from bugbug.models import load_model
from bugbug_http import ALLOW_MISSING_MODELS
from bugbug_http.utils import get_hgmo_stack

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


MODEL_LAST_LOADED: Dict[str, datetime] = {}
MODEL_CACHE: Dict[str, Model] = {}


redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost/0"))


def get_model(model_name):
    if model_name not in MODEL_CACHE:
        LOGGER.info("Recreating the %r model in cache" % model_name)
        try:
            model = load_model(model_name)
        except FileNotFoundError:
            if ALLOW_MISSING_MODELS:
                LOGGER.info(
                    "Missing %r model, skipping because ALLOW_MISSING_MODELS is set"
                    % model_name
                )
                return None
            else:
                raise

        # Cache the model only if it was last used less than one hour ago.
        if model_name in MODEL_LAST_LOADED and MODEL_LAST_LOADED[
            model_name
        ] > datetime.now() - relativedelta(hours=1):
            MODEL_CACHE[model_name] = model
    else:
        model = MODEL_CACHE[model_name]

    MODEL_LAST_LOADED[model_name] = datetime.now()
    return model


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
        encoded_data = json.dumps({"available": False})
        setkey(job.result_key, encoded_data)

    if not bugs:
        return "NOK"

    model = get_model(model_name)

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

        encoded_data = json.dumps(data)

        job = JobInfo(classify_bug, model_name, bug_id)
        setkey(job.result_key, encoded_data)

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
        os.environ.get("TEST_SELECTION_CONFIDENCE_THRESHOLD", 0.5)
    )

    # Analyze patches.
    commits = repository.download_commits(
        REPO_DIR, revs=revs, save=False, use_single_process=True
    )

    commit_data = commit_features.merge_commits(commits)

    def get_runnables(granularity):
        past_failures_data = test_scheduling.get_past_failures(granularity)

        push_num = past_failures_data["push_num"]
        all_runnables = past_failures_data["all_runnables"]

        commit_tests = []
        for data in test_scheduling.generate_data(
            past_failures_data, commit_data, push_num, all_runnables, [], []
        ):
            if granularity == "label" and not data["name"].startswith("test-"):
                continue

            commit_test = commit_data.copy()
            commit_test["test_job"] = data
            commit_tests.append(commit_test)

        probs = get_model(f"test{granularity}select").classify(
            commit_tests, probabilities=True
        )
        selected_indexes = np.argwhere(probs[:, 1] > test_selection_threshold)[:, 0]
        return [commit_tests[i]["test_job"]["name"] for i in selected_indexes]

    data = {
        "tasks": get_runnables("label"),
        "groups": get_runnables("group"),
    }
    setkey(job.result_key, json.dumps(data))

    return "OK"
