# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import logging
import os
from typing import Dict
from urllib.request import urlretrieve

import requests
from redis import Redis

from bugbug import bugzilla, get_bugbug_version
from bugbug.model import Model
from bugbug.models import load_model
from bugbug.utils import zstd_decompress

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()

MODELS_NAMES = [
    "defectenhancementtask",
    "component",
    "regression",
    "stepstoreproduce",
    "spambug",
]
MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
BASE_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.train_{}.latest/artifacts/public"
DEFAULT_EXPIRATION_TTL = 7 * 24 * 3600  # A week


MODEL_CACHE: Dict[str, Model] = {}

ALLOW_MISSING_MODELS = bool(int(os.environ.get("BUGBUG_ALLOW_MISSING_MODELS", "0")))


def result_key(model_name, bug_id):
    return f"result_{model_name}_{bug_id}"


def change_time_key(model_name, bug_id):
    return f"bugbug:change_time_{model_name}_{bug_id}"


def get_model(model_name):
    if model_name not in MODEL_CACHE:
        print("Recreating the %r model in cache" % model_name)
        try:
            model = load_model(model_name, MODELS_DIR)
        except FileNotFoundError:
            if ALLOW_MISSING_MODELS:
                print(
                    "Missing %r model, skipping because ALLOW_MISSING_MODELS is set"
                    % model_name
                )
                return None
            else:
                raise

        MODEL_CACHE[model_name] = model
        return model

    return MODEL_CACHE[model_name]


def preload_models():
    for model in MODELS_NAMES:
        get_model(model)


def retrieve_model(name):
    os.makedirs(MODELS_DIR, exist_ok=True)

    file_name = f"{name}model"
    file_path = os.path.join(MODELS_DIR, file_name)

    base_model_url = BASE_URL.format(name, f"v{get_bugbug_version()}")
    model_url = f"{base_model_url}/{file_name}.zst"
    LOGGER.info(f"Checking ETAG of {model_url}")

    r = requests.head(model_url, allow_redirects=True)
    r.raise_for_status()
    new_etag = r.headers["ETag"]

    try:
        with open(f"{file_path}.etag", "r") as f:
            old_etag = f.read()
    except IOError:
        old_etag = None

    if old_etag != new_etag:
        LOGGER.info(f"Downloading the model from {model_url}")
        urlretrieve(model_url, f"{file_path}.zst")

        zstd_decompress(file_path)
        LOGGER.info(f"Written model in {file_path}")

        with open(f"{file_path}.etag", "w") as f:
            f.write(new_etag)
    else:
        LOGGER.info(f"ETAG for {model_url} is ok")

    return file_path


def classify_bug(
    model_name, bug_ids, bugzilla_token, expiration=DEFAULT_EXPIRATION_TTL
):
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

        redis_key = result_key(model_name, bug_id)

        redis.set(redis_key, encoded_data)
        redis.expire(redis_key, expiration)

        # Save the bug last change
        change_key = change_time_key(model_name, bug_id)
        redis.set(change_key, bugs[bug_id]["last_change_time"])

    return "OK"
