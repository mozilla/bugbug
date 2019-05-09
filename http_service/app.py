# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import logging
import os
from collections import UserDict, defaultdict
from multiprocessing.pool import AsyncResult, Pool

from flask import Flask, jsonify, request
from redis import Redis
from rq import Queue

from .models import classify_bug

API_TOKEN = "X-Api-Key"

application = Flask(__name__)
redis_conn = Redis(host="localhost")
q = Queue(connection=redis_conn)  # no args implies the default queue

BUGZILLA_TOKEN = os.environ.get("BUGBUG_BUGZILLA_TOKEN")

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()


POOL = Pool(4)


class Database(UserDict):
    # We might want to customize the database with cache invalidation or plug
    # it into a real database / caching service
    pass


MODELS_DB = defaultdict(Database)


def schedule_bug_classification(model_name, bug_id):
    """ Schedule the classification of a single bug_id
    """
    res = q.enqueue(classify_bug, model_name, bug_id, BUGZILLA_TOKEN)

    res = {}

    MODELS_DB[model_name][bug_id] = res


def get_bug_classification(model_name, bug_id):
    bug_result = MODELS_DB[model_name].get(bug_id)

    if isinstance(bug_result, AsyncResult):
        if bug_result.ready():
            real_result = bug_result.get()

            MODELS_DB[model_name][bug_id] = real_result
            bug_result = real_result
        else:
            # Indicate that the result is not yet ready
            bug_result = {"ready": False}

    return bug_result or {"ready": False}


@application.route("/<model_name>/predict/<bug_id>")
def model_prediction(model_name, bug_id):
    headers = request.headers
    redis_conn.ping()

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify({"message": "Error, missing X-API-KEY"}), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    if bug_id not in MODELS_DB[model_name]:
        schedule_bug_classification(model_name, bug_id)

    data = get_bug_classification(model_name, bug_id)

    return jsonify(**data)


@application.route("/<model_name>/predict/batch", methods=["POST"])
def batch_prediction(model_name):
    headers = request.headers

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify({"message": "Error, missing X-API-KEY"}), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    # TODO Check is JSON is valid and validate against a request schema
    batch_body = json.loads(request.data)

    bugs = batch_body["bugs"]

    for bug in bugs:
        if bug not in MODELS_DB[model_name]:
            schedule_bug_classification(model_name, bug)

    data = {}

    for bug in bugs:
        data[bug] = get_bug_classification(model_name, bug)

    return jsonify(**data)
