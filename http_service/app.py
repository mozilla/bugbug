# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import logging
import os
import uuid

from flask import Flask, jsonify, request
from redis import Redis
from rq import Queue

from .models import classify_bug

API_TOKEN = "X-Api-Key"

application = Flask(__name__)
redis_url = os.environ.get("REDIS_URL", "redis://localhost/0")
redis_conn = Redis.from_url(redis_url)
q = Queue(connection=redis_conn)  # no args implies the default queue

BUGZILLA_TOKEN = os.environ.get("BUGBUG_BUGZILLA_TOKEN")

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()


def get_job_id(model_name, bug_id):
    return uuid.uuid4().hex


def schedule_bug_classification(model_name, bug_ids):
    """ Schedule the classification of a bug_id list
    """

    job_id = get_job_id(model_name, bug_ids)

    print("Scheduling", bug_ids)
    q.enqueue(classify_bug, model_name, bug_ids, BUGZILLA_TOKEN, job_id=job_id)


def is_running(model_name, bug_id):
    return False


def get_bug_classification(model_name, bug_id):
    redis_key = f"result_{model_name}_{bug_id}"
    result = redis_conn.get(redis_key)

    if result:
        return json.loads(result)

    return None


@application.route("/<model_name>/predict/<bug_id>")
def model_prediction(model_name, bug_id):
    headers = request.headers
    redis_conn.ping()

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify({"message": "Error, missing X-API-KEY"}), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    data = get_bug_classification(model_name, bug_id)

    if not data:
        schedule_bug_classification(model_name, [bug_id])
        data = {"ready": False}

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

    data = {}
    missing_bugs = []

    for bug in bugs:
        data[bug] = get_bug_classification(model_name, bug)
        if not data[bug]:

            if not is_running(model_name, bug):
                missing_bugs.append(bug)
            data[bug] = {"ready": False}

    if missing_bugs:
        print("Scheduling call for missing bugs")
        schedule_bug_classification(model_name, missing_bugs)

    return jsonify(**data)
