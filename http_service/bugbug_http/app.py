# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import gzip
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

import orjson
import zstandard
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from apispec_webframeworks.flask import FlaskPlugin
from cerberus import Validator
from flask import Flask, Response, jsonify, render_template, request
from flask_cors import cross_origin
from libmozdata.bugzilla import Bugzilla
from marshmallow import Schema, fields
from redis import Redis
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sentry_sdk.integrations.flask import FlaskIntegration

from bugbug import bugzilla, get_bugbug_version, utils
from bugbug_http.models import (
    MODELS_NAMES,
    classify_broken_site_report,
    classify_bug,
    classify_issue,
    get_config_specific_groups,
    schedule_tests,
)
from bugbug_http.sentry import setup_sentry

if os.environ.get("SENTRY_DSN"):
    setup_sentry(dsn=os.environ.get("SENTRY_DSN"), integrations=[FlaskIntegration()])

utils.setup_libmozdata()

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
url = urlparse(os.environ.get("REDIS_URL", "redis://localhost/0"))
assert url.hostname is not None
redis_conn = Redis(
    host=url.hostname,
    port=url.port if url.port is not None else 6379,
    password=url.password,
    ssl=True if url.scheme == "rediss" else False,
    ssl_cert_reqs=None,
)

# Kill jobs which don't finish within 12 minutes.
JOB_TIMEOUT = 12 * 60
# Kill Bugzilla jobs which don't finish within 5 minutes.
BUGZILLA_JOB_TIMEOUT = 5 * 60
# Remove jobs from the queue if they haven't started within 7 minutes.
QUEUE_TIMEOUT = 7 * 60
# Store the information that a job failed for 3 minutes.
FAILURE_TTL = 3 * 60

q = Queue(
    connection=redis_conn, default_timeout=JOB_TIMEOUT
)  # no args implies the default queue
VALIDATOR = Validator()

BUGZILLA_TOKEN = os.environ.get("BUGBUG_BUGZILLA_TOKEN")
GITHUB_TOKEN = os.environ.get("BUGBUG_GITHUB_TOKEN")

dctx = zstandard.ZstdDecompressor()

logging.basicConfig(level=logging.DEBUG)
LOGGER = logging.getLogger()


class BugPrediction(Schema):
    prob = fields.List(fields.Float())
    index = fields.Integer()
    suggestion = fields.Str()
    extra_data = fields.Dict()


class NotAvailableYet(Schema):
    ready = fields.Boolean(enum=[False])


class ModelName(Schema):
    model_name = fields.Str(enum=MODELS_NAMES, example="component")


class UnauthorizedError(Schema):
    message = fields.Str(default="Error, missing X-API-KEY")


class BranchName(Schema):
    branch = fields.Str(example="autoland")


class Schedules(Schema):
    tasks = fields.List(fields.Str)
    groups = fields.List(fields.Str)


spec.components.schema(BugPrediction.__name__, schema=BugPrediction)
spec.components.schema(NotAvailableYet.__name__, schema=NotAvailableYet)
spec.components.schema(ModelName.__name__, schema=ModelName)
spec.components.schema(UnauthorizedError.__name__, schema=UnauthorizedError)
spec.components.schema(BranchName.__name__, schema=BranchName)
spec.components.schema(Schedules.__name__, schema=Schedules)


api_key_scheme = {"type": "apiKey", "in": "header", "name": "X-API-Key"}
spec.components.security_scheme("api_key", api_key_scheme)


@dataclass(init=False, frozen=True)
class JobInfo:
    func: Callable[..., str]
    args: Sequence[Any] = field(default_factory=list)

    def __init__(self, func, *args):
        # Custom __init__ is needed to support *args, and object.__setattr__ is
        # needed for 'frozen=True'.
        object.__setattr__(self, "func", func)
        object.__setattr__(self, "args", args)

    def __str__(self):
        return f"{self.func.__name__}:{'_'.join(map(str, self.args))}"

    @property
    def mapping_key(self):
        """The mapping key for this job.

        Returns:
            (str) A key to be used to for job ids.
        """
        return f"bugbug:job_id:{self}"

    @property
    def result_key(self):
        """The result key for this job.

        Returns:
            (str) A key to be used to for job results.
        """
        return f"bugbug:job_result:{self}"

    @property
    def change_time_key(self):
        """The change time key for this job.

        Returns:
            (str) A key to be used to for job change time.
        """
        return f"bugbug:change_time:{self}"


def get_job_id() -> str:
    return uuid.uuid4().hex


def init_job(job: JobInfo, job_id: str | None = None) -> str:
    job_id = job_id or get_job_id()
    redis_conn.mset({job.mapping_key: job_id})
    return job_id


def schedule_job(
    job: JobInfo, job_id: str | None = None, timeout: int | None = None
) -> None:
    job_id = init_job(job, job_id)

    q.enqueue(
        job.func,
        *job.args,
        job_id=job_id,
        job_timeout=timeout,
        ttl=QUEUE_TIMEOUT,
        failure_ttl=FAILURE_TTL,
    )


def prepare_queue_job(
    job: JobInfo, job_id: str | None = None, timeout: int | None = None
) -> Queue:
    job_id = init_job(job, job_id)
    return Queue.prepare_data(
        job.func,
        args=job.args,
        job_id=job_id,
        timeout=timeout,
        ttl=QUEUE_TIMEOUT,
        failure_ttl=FAILURE_TTL,
    )


def create_bug_classification_jobs(
    model_name: str, bug_ids: Sequence[int]
) -> tuple[JobInfo, str, int]:
    """Create job_id and redis connection"""
    job_id = get_job_id()

    # Set the mapping before queuing to avoid some race conditions
    job_id_mapping = {}
    for bug_id in bug_ids:
        key = JobInfo(classify_bug, model_name, bug_id).mapping_key
        job_id_mapping[key] = job_id

    redis_conn.mset(job_id_mapping)

    return (
        JobInfo(classify_bug, model_name, bug_ids, BUGZILLA_TOKEN),
        job_id,
        BUGZILLA_JOB_TIMEOUT,
    )


def create_broken_site_report_classification_jobs(
    model_name: str, reports: list[dict]
) -> tuple[JobInfo, str, int]:
    """Create job_id and redis connection"""
    job_id = get_job_id()

    # Set the mapping before queuing to avoid some race conditions
    job_id_mapping = {}
    for report in reports:
        key = JobInfo(
            classify_broken_site_report, model_name, report["uuid"]
        ).mapping_key
        job_id_mapping[key] = job_id

    redis_conn.mset(job_id_mapping)

    return (
        JobInfo(classify_broken_site_report, model_name, reports),
        job_id,
        JOB_TIMEOUT,
    )


def schedule_issue_classification(
    model_name: str, owner: str, repo: str, issue_nums: Sequence[int]
) -> None:
    """Schedule the classification of a issue_id list"""
    job_id = get_job_id()

    # Set the mapping before queuing to avoid some race conditions
    job_id_mapping = {}
    for issue_num in issue_nums:
        key = JobInfo(classify_issue, model_name, owner, repo, issue_num).mapping_key
        job_id_mapping[key] = job_id

    redis_conn.mset(job_id_mapping)

    schedule_job(
        JobInfo(classify_issue, model_name, owner, repo, issue_nums),
        job_id=job_id,
        timeout=BUGZILLA_JOB_TIMEOUT,
    )


def is_pending(job):
    # Check if there is a job
    job_id = redis_conn.get(job.mapping_key)

    if not job_id:
        LOGGER.debug(f"No job ID mapping {job_id}, False")
        return False

    try:
        job = Job.fetch(job_id.decode("ascii"), connection=redis_conn)
    except NoSuchJobError:
        LOGGER.debug(f"No job in DB for {job_id}, False")
        # The job might have expired from redis
        return False

    job_status = job.get_status()
    if job_status == "started":
        LOGGER.debug(f"Job {job_id} is running, True")
        return True

    # Enforce job timeout as RQ doesn't seems to do it https://github.com/rq/rq/issues/758
    timeout_datetime = job.enqueued_at + timedelta(seconds=job.timeout)
    utcnow = datetime.utcnow()
    if timeout_datetime < utcnow:
        # Remove the timeouted job so it will be requeued
        job.cancel()
        job.cleanup()

        LOGGER.debug(f"Job timeout {job_id}, False")

        return False

    if job_status == "queued":
        LOGGER.debug(f"Job {job_id} is queued, True")
        return True

    LOGGER.debug(f"Job {job_id} has status {job_status}, False")

    return False


def get_bugs_last_change_time(bug_ids):
    bugzilla.set_token(BUGZILLA_TOKEN)

    old_CHUNK_SIZE = Bugzilla.BUGZILLA_CHUNK_SIZE
    try:
        Bugzilla.BUGZILLA_CHUNK_SIZE = 700

        bugs = {}

        def bughandler(bug):
            bugs[bug["id"]] = bug["last_change_time"]

        Bugzilla(
            bugids=bug_ids,
            bughandler=bughandler,
            include_fields=["id", "last_change_time"],
        ).get_data().wait()
    finally:
        Bugzilla.BUGZILLA_CHUNK_SIZE = old_CHUNK_SIZE

    return bugs


def get_github_issues_update_time(
    owner: str, repo: str, issue_nums: Sequence[int]
) -> dict:
    header = {"Authorization": "token {}".format(GITHUB_TOKEN)}
    repo_url = f"https://api.github.com/repos/{owner}/{repo}/issues/"

    issues = {}
    for issue_num in issue_nums:
        issue_url = repo_url + str(issue_num)
        response = utils.get_session("github").get(issue_url, headers=header)
        response.raise_for_status()
        raw_issue = response.json()
        issues[raw_issue["number"]] = raw_issue["updated_at"]

    return issues


def is_prediction_invalidated(job, change_time):
    # First get the saved change time
    saved_change_time = redis_conn.get(job.change_time_key)

    # If we have no last changed time, the bug was not classified yet or the bug was classified by an old worker
    if not saved_change_time:
        # We can have a result without a cache time
        if redis_conn.exists(job.result_key):
            return True

        return False

    return saved_change_time.decode("utf-8") != change_time


def clean_prediction_cache(job):
    # If the bug was modified since last time we classified it, clear the cache to avoid stale answer
    LOGGER.debug(f"Cleaning results for {job}")

    redis_conn.delete(job.result_key)
    redis_conn.delete(job.change_time_key)


def get_result(job: JobInfo) -> Any | None:
    LOGGER.debug(f"Checking for existing results at {job.result_key}")
    result = redis_conn.get(job.result_key)

    if result:
        LOGGER.debug(f"Found {result!r}")
        try:
            result = dctx.decompress(result)
        except zstandard.ZstdError:
            # Some job results were stored before compression was enabled.
            # We can remove the exception handling after enough time has passed
            # since 47114f4f47db6b73214cf946377be8da945d34b5.
            pass

        assert result is not None  # mypy thinks it could be None
        return orjson.loads(result)

    return None


def compress_response(data: dict, status_code: int):
    """Compress data using gzip compressor and frame response

    :param data: data
    :type data: dict
    :param status_code: response status code
    :type status_code: int
    :return: response with gzip compressed data
    :rtype: Response
    """

    gzip_buffer = gzip.compress(orjson.dumps(data), compresslevel=9)

    response = Response(status=status_code)
    response.set_data(gzip_buffer)
    response.headers["Content-Encoding"] = "gzip"
    response.headers["Content-Length"] = len(gzip_buffer)
    response.headers["Content-Type"] = "application/json"

    return response


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
              schema: NotAvailableYet
        401:
          description: API key is missing
          content:
            application/json:
              schema: UnauthorizedError
    """
    headers = request.headers

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify(UnauthorizedError().dump({})), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    if model_name not in MODELS_NAMES:
        return jsonify({"error": f"Model {model_name} doesn't exist"}), 404

    # Get the latest change from Bugzilla for the bug
    bug = get_bugs_last_change_time([bug_id])

    # Change time could be None if it's a security bug
    job = JobInfo(classify_bug, model_name, bug_id)
    bug_change_time = bug.get(bug_id)
    if bug_change_time and is_prediction_invalidated(job, bug[bug_id]):
        clean_prediction_cache(job)

    status_code = 200
    data = get_result(job)

    if not data:
        if not is_pending(job):
            job_info, job_id, timeout = create_bug_classification_jobs(
                model_name, [bug_id]
            )
            schedule_job(job_info, job_id=job_id, timeout=timeout)
        status_code = 202
        data = {"ready": False}

    return compress_response(data, status_code)


@application.route(
    "/<model_name>/predict/github/<string:owner>/<string:repo>/<int:issue_num>"
)
@cross_origin()
def model_prediction_github(model_name, owner, repo, issue_num):
    """
    ---
    get:
      description: Classify a single issue using given model, answer either 200 if the issue is processed or 202 if the issue is being processed
      summary: Classify a single issue
      parameters:
      - name: model_name
        in: path
        schema: ModelName
      - name: owner
        in: path
        schema:
          type: str
          example: webcompat
      - name: repo
        in: path
        schema:
          type: str
          example: web-bugs
      - name: issue_number
        in: path
        schema:
          type: integer
          example: 123456
      responses:
        200:
          description: A single issue prediction
          content:
            application/json:
              schema: BugPrediction
        202:
          description: A temporary answer for the issue being processed
          content:
            application/json:
              schema: NotAvailableYet
        401:
          description: API key is missing
          content:
            application/json:
              schema: UnauthorizedError
    """
    headers = request.headers

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify(UnauthorizedError().dump({})), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    if model_name not in MODELS_NAMES:
        return jsonify({"error": f"Model {model_name} doesn't exist"}), 404

    # Get the latest change date from github for the issue
    update_time = get_github_issues_update_time(owner, repo, [issue_num])

    job = JobInfo(classify_issue, model_name, owner, repo, issue_num)
    issue_change_time = update_time.get(issue_num)
    if issue_change_time and is_prediction_invalidated(job, update_time[issue_num]):
        clean_prediction_cache(job)

    status_code = 200
    data = get_result(job)

    if not data:
        if not is_pending(job):
            schedule_issue_classification(model_name, owner, repo, [issue_num])
        status_code = 202
        data = {"ready": False}

    return compress_response(data, status_code)


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
        return jsonify(UnauthorizedError().dump({})), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    if model_name not in MODELS_NAMES:
        return jsonify({"error": f"Model {model_name} doesn't exist"}), 404

    # TODO Check is JSON is valid and validate against a request schema
    batch_body = orjson.loads(request.data)

    # Validate
    schema = {
        "bugs": {
            "type": "list",
            "minlength": 1,
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

    bug_change_dates = get_bugs_last_change_time(bugs)

    for bug_id in bugs:
        job = JobInfo(classify_bug, model_name, bug_id)

        change_time = bug_change_dates.get(int(bug_id))
        # Change time could be None if it's a security bug
        if change_time and is_prediction_invalidated(job, change_time):
            clean_prediction_cache(job)

        data[str(bug_id)] = get_result(job)
        if not data[str(bug_id)]:
            if not is_pending(job):
                missing_bugs.append(bug_id)
            status_code = 202
            data[str(bug_id)] = {"ready": False}

    queueJobList: Queue = []

    for i in range(0, len(missing_bugs), 100):
        bug_ids = missing_bugs[i : (i + 100)]
        job_info, job_id, timeout = create_bug_classification_jobs(model_name, bug_ids)
        queueJobList.append(prepare_queue_job(job_info, job_id=job_id, timeout=timeout))
    q.enqueue_many(queueJobList)

    return compress_response({"bugs": data}, status_code)


@application.route("/<model_name>/predict/broken_site_report/batch", methods=["POST"])
@cross_origin()
def batch_prediction_broken_site_report(model_name):
    """
    ---
    post:
      description: >
        Post a batch of reports to classify, answer either 200 if all are are
        processed or 202 if at least one report is not processed.
        <br/><br/>
        Starts by sending a batch of reports like this:<br/>
        ```
        {"reports": [{"uuid": "954dbc23-91e6-4d6f-a10a-405f46663e31", "title: "https://example.com", "body": "Loads blank page."}]}
        ```<br/><br>

        You will likely get a 202 answer that indicates that no result is
        available yet for any of the reports id you provided with the following
        body:<br/>

        ```
        {"reports": {"<uuid 1>": {ready: False}, "<uuid 2>": {ready: False}}}
        ```<br/><br/>

        Call back the same endpoint with the same uuids a bit it later, and you
        will get the results.<br/><br/>

        You might get the following output if some bugs are not available:
        <br/>

        ```
        {"reports": {"<uuid 1>": {"available": False}}}
        ```<br/><br/>

        And you will get the following output once the bugs are available:
        <br/>
        ```
        {"reports": {"<uuid 1>": {"extra_data": {}, "index": 0, "prob": [0], "suggestion": ""}}}
        ```<br/><br/>

        Please be aware that each report could be in a different state, so the
        following output, where a report is returned and another one is still
        being processed, is valid:
        <br/>
        ```
        {"reports": {"<uuid 1>": {"available": False}, "<uuid 2>": {"extra_data": {}, "index": 0, "prob": [0], "suggestion": ""}}}
        ```
      summary: Classify a batch of reports
      parameters:
      - name: model_name
        in: path
        schema: ModelName
      requestBody:
        description: The list of reports to classify
        content:
          application/json:
            schema:
              type: object
              properties:
              reports:
                type: array
                items:
                  type: object
                  properties:
                    uuid:
                      type: string
                    title:
                      type: string
                    body:
                      type: string
            examples:
              cat:
                summary: An example of payload
                value:
                  reports:
                    - uuid: "954dbc23-91e6-4d6f-a10a-405f46663e31"
                      title: "https://example.com"
                      body: "Loads blank page."
      responses:
        200:
          description: A list of results
          content:
            application/json:
              schema:
                type: object
                additionalProperties: true
                example:
                  reports:
                    <uuid 1>:
                      extra_data: {}
                      index: 0
                      prob: [0]
                      suggestion: string
                    <uuid 2>:
                      extra_data: {}
                      index: 0
                      prob: [0]
                      suggestion: string
        202:
          description: A temporary answer for reports being processed
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
                  reports:
                    <uuid 1>:
                      extra_data: {}
                      index: 0
                      prob: [0]
                      suggestion: string
                    <uuid 2>: {ready: False}
        401:
          description: API key is missing
          content:
            application/json:
              schema: UnauthorizedError
    """
    headers = request.headers

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify(UnauthorizedError().dump({})), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    if model_name not in MODELS_NAMES:
        return jsonify({"error": f"Model {model_name} doesn't exist"}), 404

    batch_body = orjson.loads(request.data)

    schema = {
        "reports": {
            "type": "list",
            "minlength": 1,
            "schema": {
                "type": "dict",
                "schema": {
                    "uuid": {"type": "string", "required": True},
                    "title": {"type": "string", "required": True},
                    "body": {"type": "string", "required": True},
                },
            },
        }
    }
    validator = Validator()
    if not validator.validate(batch_body, schema):
        return jsonify({"errors": validator.errors}), 400

    reports = batch_body["reports"]

    status_code = 200
    data = {}
    missing_reports = []

    for report in reports:
        report_uuid = report["uuid"]
        job = JobInfo(classify_broken_site_report, model_name, report_uuid)

        data[report_uuid] = get_result(job)
        if not data[report_uuid]:
            if not is_pending(job):
                missing_reports.append(report)
            status_code = 202
            data[report_uuid] = {"ready": False}

    queueJobList: Queue = []

    for i in range(0, len(missing_reports), 100):
        reports = missing_reports[i : (i + 100)]
        job_info, job_id, timeout = create_broken_site_report_classification_jobs(
            model_name, reports
        )
        queueJobList.append(prepare_queue_job(job_info, job_id=job_id, timeout=timeout))
    q.enqueue_many(queueJobList)

    return compress_response({"reports": data}, status_code)


@application.route("/push/<path:branch>/<rev>/schedules")
@cross_origin()
def push_schedules(branch, rev):
    """
    ---
    get:
      description: Determine which tests and tasks a push should schedule.
      summary: Get which tests and tasks to schedule.
      parameters:
      - name: branch
        in: path
        schema:
          BranchName
      - name: rev
        in: path
        schema:
          type: str
          example: 76383a875678
      responses:
        200:
          description: A dict of tests and tasks to schedule.
          content:
            application/json:
              schema: Schedules
        202:
          description: Request is still being processed.
          content:
            application/json:
              schema: NotAvailableYet
        401:
          description: API key is missing
          content:
            application/json:
              schema: UnauthorizedError
    """
    headers = request.headers

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify(UnauthorizedError().dump({})), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    # Support the string 'autoland' for convenience.
    if branch == "autoland":
        branch = "integration/autoland"

    job = JobInfo(schedule_tests, branch, rev)
    data = get_result(job)
    if data:
        return compress_response(data, 200)

    if not is_pending(job):
        schedule_job(job)
    return jsonify({"ready": False}), 202


@application.route("/config_specific_groups/<path:config>")
@cross_origin()
def config_specific_groups(config: str) -> tuple[Response, int]:
    """
    ---
    get:
      description: Determine which groups could possibly exclusively fail on the given configuration.
      summary: Get config-specific groups.
      parameters:
      - name: config
        in: path
        schema:
          type: str
          example: test-windows7-32/opt-*-e10s
      responses:
        200:
          description: A list of groups that could specifically fail on the given configuration.
          content:
            application/json:
              schema:
                type: array
                items:
                  type: string
        202:
          description: Request is still being processed.
          content:
            application/json:
              schema: NotAvailableYet
        401:
          description: API key is missing
          content:
            application/json:
              schema: UnauthorizedError
    """
    headers = request.headers

    auth = headers.get(API_TOKEN)

    if not auth:
        return jsonify(UnauthorizedError().dump({})), 401
    else:
        LOGGER.info("Request with API TOKEN %r", auth)

    job = JobInfo(get_config_specific_groups, config)
    data = get_result(job)
    if data is not None:
        return compress_response(data, 200)

    if not is_pending(job):
        schedule_job(job)
    return jsonify({"ready": False}), 202


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
