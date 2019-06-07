# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import logging
import lzma
import os
import shutil
from urllib.request import urlretrieve

import requests
from redis import Redis

from bugbug import bugzilla, get_bugbug_version
from bugbug.models import load_model as bugbug_load_model

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()

MODELS_NAMES = ["defectenhancementtask", "component", "regression"]
MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
BASE_URL = "https://index.taskcluster.net/v1/task/project.relman.bugbug.train_{}.latest/artifacts/public"
DEFAULT_EXPIRATION_TTL = 7 * 24 * 3600  # A week


def load_model(model):
    # TODO: Do not crash when the asked model is not one of the trained models
    return bugbug_load_model(model, MODELS_DIR)


def retrieve_model(name):
    os.makedirs(MODELS_DIR, exist_ok=True)

    file_name = f"{name}model"
    file_path = os.path.join(MODELS_DIR, file_name)

    base_model_url = BASE_URL.format(name, f"v{get_bugbug_version()}")
    model_url = f"{base_model_url}/{file_name}.xz"
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
        urlretrieve(model_url, f"{file_path}.xz")

        with lzma.open(f"{file_path}.xz", "rb") as input_f:
            with open(file_path, "wb") as output_f:
                shutil.copyfileobj(input_f, output_f)
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
    bugs = bugzilla._download(bug_ids)

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

    # TODO: Cache the model in the process memory, it's quite hard as the RQ
    # worker is forking before starting
    model = load_model(model_name)

    # TODO: Classify could choke on a single bug which could make the whole
    # job to fails. What should we do here?
    probs = model.classify(list(bugs.values()), True)
    indexes = probs.argmax(axis=-1)
    suggestions = model.clf._le.inverse_transform(indexes)

    probs_list = probs.tolist()
    indexes_list = indexes.tolist()
    suggestions_list = suggestions.tolist()

    for i, bug_id in enumerate(bugs.keys()):
        data = {
            "probs": probs_list[i],
            "indexes": indexes_list[i],
            "suggestions": suggestions_list[i],
        }

        encoded_data = json.dumps(data)

        redis_key = f"result_{model_name}_{bug_id}"

        redis.set(redis_key, encoded_data)
        redis.expire(redis_key, expiration)

    return "OK"
