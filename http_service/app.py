# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import logging
import os
import uuid

from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from apispec_webframeworks.flask import FlaskPlugin
from cerberus import Validator
from flask import Flask, jsonify, render_template, request
from flask_cors import cross_origin
from marshmallow import Schema, fields
from redis import Redis
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job

from bugbug import get_bugbug_version

from .models import MODELS_NAMES, classify_bug

API_TOKEN = "X-Api-Key"

API_DESCRIPTION = """
This is the documentation for the BubBug http service, the platform for Bugzilla Machine Learning projects.

# Introduction

This service can be used to classify a given bug using a pre-trained model.
You can classify a single bug or a batch of bugs.
The classification happens in the background so you need to call back the service for getting the results.
"""

spec = APISpec(
    title="Bugbug",
    version=get_bugbug_version(),
    openapi_version="3.0.2",
    info=dict(description=API_DESCRIPTION),
    plugins=[FlaskPlugin(), MarshmallowPlugin()],
    security=[{"api_key": []}],
)

application = Flask(__name__)
redis_url = os.environ.get("REDIS_URL", "redis://localhost/0")
redis_conn = Redis.from_url(redis_url)
q = Queue(connection=redis_conn)  # no args implies the default queue
VALIDATOR = Validator()

BUGZILLA_TOKEN = os.environ.get("BUGBUG_BUGZILLA_TOKEN")

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()


class BugPrediction(Schema):
    prob = fields.List(fields.Float())
    index = fields.Integer()
    suggestion = fields.Str()
    extra_data = fields.Dict()


class BugPredictionNotAvailableYet(Schema):
    ready = fields.Boolean(enum=[False])


class ModelName(Schema):
    model_name = fields.Str(enum=MODELS_NAMES, example="component")


class UnauthorizedError(Schema):
    message = fields.Str(default="Error, missing X-API-KEY")


spec.components.schema(BugPrediction.__name__, schema=BugPrediction)
spec.components.schema(
    BugPredictionNotAvailableYet.__name__, schema=BugPredictionNotAvailableYet
)
spec.components.schema(ModelName.__name__, schema=ModelName)
spec.components.schema(UnauthorizedError.__name__, schema=UnauthorizedError)


api_key_scheme = {"type": "apiKey", "in": "header", "name": "X-API-Key"}
spec.components.security_scheme("api_key", api_key_scheme)


def get_job_id():
    return uuid.uuid4().hex


def get_mapping_key(model_name, bug_id):
    return f"bugbug:mapping_{model_name}_{bug_id}"


def schedule_bug_classification(model_name, bug_ids):
    """ Schedule the classification of a bug_id list
    """

    job_id = get_job_id()

    print("Scheduling", bug_ids)

    # Set the mapping before queuing to avoid some race conditions
    job_id_mapping = {get_mapping_key(model_name, bug_id): job_id for bug_id in bug_ids}
    redis_conn.mset(job_id_mapping)

    q.enqueue(classify_bug, model_name, bug_ids, BUGZILLA_TOKEN, job_id=job_id)


def is_running(model_name, bug_id):
    # Check if there is a job
    mapping_key = get_mapping_key(model_name, bug_id)

    job_id = redis_conn.get(mapping_key)

    if not job_id:
        return False

    try:
        job = Job.fetch(job_id.decode("utf-8"), connection=redis_conn)
    except NoSuchJobError:
        # The job might have expired from redis
        return False

    job_status = job.get_status()
    if job_status in ("running", "started", "queued"):
        print("Bug classification already running", model_name, bug_id)
        return True

    return False


def get_bug_classification(model_name, bug_id):
    redis_key = f"result_{model_name}_{bug_id}"
    result = redis_conn.get(redis_key)

    if result:
        return json.loads(result)

    return None


@application.route("/<model_name>/predict/<int:bug_id>")
@cross_origin()
def model_prediction(model_name, bug_id):
    """
    ---
    get:
      description: Classify a single bug using given model, answer either 200 if the bug is processed or 202 if the bug is being processed
      summary: Classify a single bug
      parameters:
      - name: model_name
        in: path
        schema: ModelName
      - name: bug_id
        in: path
        schema:
          type: integer
          example: 123456
      responses:
        200:
          description: A single bug prediction
          content:
            application/json:
              schema: BugPrediction
        202:
          description: A temporary answer for the bug being processed
          content:
            application/json:
              schema:
                type: object
                properties:
                  ready:
                    type: boolean
                    enum: [False]
        401:
          description: API key is missing
          content:
            application/json:
              schema: UnauthorizedError
    """
    headers = request.headers
    redis_conn.ping()

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify(UnauthorizedError().dump({}).data), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    status_code = 200
    data = get_bug_classification(model_name, bug_id)

    if not data:
        if not is_running(model_name, bug_id):
            schedule_bug_classification(model_name, [bug_id])
        status_code = 202
        data = {"ready": False}

    return jsonify(**data), status_code


@application.route("/<model_name>/predict/batch", methods=["POST"])
@cross_origin()
def batch_prediction(model_name):
    """
    ---
    post:
      description: >
        Post a batch of bug ids to classify, answer either 200 if all bugs are
        processed or 202 if at least one bug is not processed.
        <br/><br/>
        Starts by sending a batch of bugs ids like this:<br/>
        ```
        {"bugs": [123, 456]}
        ```<br/><br>

        You will likely get a 202 answer that indicates that no result is
        available yet for any of the bug id you provided with the following
        body:<br/>

        ```
        {"bugs": {"123": {ready: False}, "456": {ready: False}}}
        ```<br/><br/>

        Call back the same endpoint with the same bug ids a bit later, and you
        will get the results.<br/><br/>

        You might get the following output if some bugs are not available:
        <br/>

        ```
        {"bugs": {"123": {"available": False}}}
        ```<br/><br/>

        And you will get the following output once the bugs are available:
        <br/>
        ```
        {"bugs": {"456": {"extra_data": {}, "index": 0, "prob": [0], "suggestion": ""}}}
        ```<br/><br/>

        Please be aware that each bug could be in a different state, so the
        following output, where a bug is returned and another one is still
        being processed, is valid:
        <br/>
        ```
        {"bugs": {"123": {"available": False}, "456": {"extra_data": {}, "index": 0, "prob": [0], "suggestion": ""}}}
        ```
      summary: Classify a batch of bugs
      parameters:
      - name: model_name
        in: path
        schema: ModelName
      requestBody:
        description: The list of bugs to classify
        content:
          application/json:
            schema:
              type: object
              properties:
                bugs:
                  type: array
                  items:
                    type: integer
            examples:
              cat:
                summary: An example of payload
                value:
                  bugs:
                    [123456, 789012]
      responses:
        200:
          description: A list of results
          content:
            application/json:
              schema:
                type: object
                additionalProperties: true
                example:
                  bugs:
                    123456:
                      extra_data: {}
                      index: 0
                      prob: [0]
                      suggestion: string
                    789012:
                      extra_data: {}
                      index: 0
                      prob: [0]
                      suggestion: string
        202:
          description: A temporary answer for bugs being processed
          content:
            application/json:
              schema:
                type: object
                items:
                    type: object
                    properties:
                      ready:
                        type: boolean
                        enum: [False]
                example:
                  bugs:
                    123456:
                      extra_data: {}
                      index: 0
                      prob: [0]
                      suggestion: string
                    789012: {ready: False}
        401:
          description: API key is missing
          content:
            application/json:
              schema: UnauthorizedError
    """
    headers = request.headers

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify(UnauthorizedError().dump({}).data), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    # TODO Check is JSON is valid and validate against a request schema
    batch_body = json.loads(request.data)

    # Validate
    schema = {
        "bugs": {
            "type": "list",
            "minlength": 1,
            "maxlength": 1000,
            "schema": {"type": "integer"},
        }
    }
    validator = Validator()
    if not validator.validate(batch_body, schema):
        return jsonify({"errors": validator.errors}), 400

    bugs = batch_body["bugs"]

    status_code = 200
    data = {}
    missing_bugs = []

    for bug in bugs:
        data[str(bug)] = get_bug_classification(model_name, bug)
        if not data[str(bug)]:
            if not is_running(model_name, bug):
                missing_bugs.append(bug)
            status_code = 202
            data[str(bug)] = {"ready": False}

    if missing_bugs:
        print("Scheduling call for missing bugs")
        # TODO: We should probably schedule chunks of bugs to avoid jobs that
        # are running for too long and reduce pressure on bugzilla, it mights
        # not like getting 1 million bug at a time
        schedule_bug_classification(model_name, missing_bugs)

    return jsonify({"bugs": data}), status_code


@application.route("/swagger")
@cross_origin()
def swagger():
    for name, rule in application.view_functions.items():
        # Ignore static endpoint as it isn't documented with OpenAPI
        if name == "static":
            continue
        spec.path(view=rule)

    return jsonify(spec.to_dict())


@application.route("/doc")
def doc():
    return render_template("doc.html")
