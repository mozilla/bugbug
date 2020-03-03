# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import logging
import os
from datetime import datetime
from typing import Dict

import requests
from dateutil.relativedelta import relativedelta
from redis import Redis

from bugbug import bugzilla
from bugbug.model import Model
from bugbug.models import load_model
from bugbug_http import ALLOW_MISSING_MODELS

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
MODELS_TO_PRELOAD = [
    "component",
    "testlabelselect",
    "testgroupselect",
]
DEFAULT_EXPIRATION_TTL = 7 * 24 * 3600  # A week


MODEL_LAST_LOADED: Dict[str, datetime] = {}
MODEL_CACHE: Dict[str, Model] = {}


def get_model(model_name):
    if model_name not in MODEL_CACHE:
        print("Recreating the %r model in cache" % model_name)
        try:
            model = load_model(model_name)
        except FileNotFoundError:
            if ALLOW_MISSING_MODELS:
                print(
                    "Missing %r model, skipping because ALLOW_MISSING_MODELS is set"
                    % model_name
                )
                return None
            else:
                raise

        # Cache the model only if it was last used less than two hours ago.
        if model_name in MODEL_LAST_LOADED and MODEL_LAST_LOADED[
            model_name
        ] > datetime.now() - relativedelta(hours=2):
            MODEL_CACHE[model_name] = model
    else:
        model = MODEL_CACHE[model_name]

    MODEL_LAST_LOADED[model_name] = datetime.now()
    return model


def preload_models():
    for model in MODELS_TO_PRELOAD:
        get_model(model)


def classify_bug(
    model_name, bug_ids, bugzilla_token, expiration=DEFAULT_EXPIRATION_TTL
):
    from bugbug_http.app import JobInfo

    # This should be called in a process worker so it should be safe to set
    # the token here
    bug_ids_set = set(map(int, bug_ids))
    bugzilla.set_token(bugzilla_token)
    bugs = bugzilla.get(bug_ids)

    redis_url = os.environ.get("REDIS_URL", "redis://localhost/0")
    redis = Redis.from_url(redis_url)

    missing_bugs = bug_ids_set.difference(bugs.keys())

    for bug_id in missing_bugs:
        redis_key = f"result_{model_name}_{bug_id}"

        # TODO: Find a better error format
        encoded_data = json.dumps({"available": False})

        redis.set(redis_key, encoded_data)
        redis.expire(redis_key, expiration)

    if not bugs:
        return "NOK"

    model = get_model(model_name)

    if not model:
        print("Missing model %r, aborting" % model_name)
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
        redis_key = job.result_key

        redis.set(redis_key, encoded_data)
        redis.expire(redis_key, expiration)

        # Save the bug last change
        redis.set(job.change_time_key, bugs[bug_id]["last_change_time"])

    return "OK"


def schedule_tests(branch, rev, expiration=DEFAULT_EXPIRATION_TTL):
    from bugbug_http.app import JobInfo

    job = JobInfo(schedule_tests, branch, rev)
    LOGGER.debug("Processing {job}")

    url = f"https://hg.mozilla.org/{branch}/json-automationrelevance/{rev}"
    r = requests.get(url)

    if r.status_code == 404:
        LOGGER.warning(f"Push not found at {url}!")
        return "NOK"

    first_rev = r.json()["changesets"][0]["node"]
    if first_rev != rev:
        revset = f"{first_rev}::{rev}"
    else:
        revset = rev

    # TODO Return real data based on 'revset'
    def get_data(revset):
        return {
            "tasks": ["test-macosx1014-64/debug-gtest-1proc"],
            "groups": [
                "caps/test/unit/xpcshell.ini",
                "dom/indexedDB/test/mochitest.ini",
            ],
        }

    data = get_data(revset)
    encoded_data = json.dumps(data)

    redis_url = os.environ.get("REDIS_URL", "redis://localhost/0")

    redis_key = job.result_key
    redis = Redis.from_url(redis_url)
    redis.set(redis_key, encoded_data)
    redis.expire(redis_key, expiration)

    return "OK"
