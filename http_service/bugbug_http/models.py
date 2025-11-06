# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os
from datetime import timedelta
from functools import lru_cache
from typing import Sequence
from urllib.parse import urlparse

import orjson
import requests
import zstandard
from redis import Redis

from bugbug import bugzilla, repository, test_scheduling, utils
from bugbug.github import Github
from bugbug.model import Model
from bugbug.models import testselect
from bugbug.utils import get_hgmo_stack
from bugbug_http.readthrough_cache import ReadthroughTTLCache

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger()

MODELS_NAMES = [
    "defectenhancementtask",
    "component",
    "invalidcompatibilityreport",
    "needsdiagnosis",
    "regression",
    "stepstoreproduce",
    "spambug",
    "testlabelselect",
    "testgroupselect",
    "accessibility",
    "performancebug",
    "worksforme",
    "fenixcomponent",
]

DEFAULT_EXPIRATION_TTL = 7 * 24 * 3600  # A week
url = urlparse(os.environ.get("REDIS_URL", "redis://localhost/0"))
assert url.hostname is not None
redis = Redis(
    host=url.hostname,
    port=url.port if url.port is not None else 6379,
    password=url.password,
    ssl=True if url.scheme == "rediss" else False,
    ssl_cert_reqs=None,
)

MODEL_CACHE: ReadthroughTTLCache[str, Model] = ReadthroughTTLCache(
    timedelta(hours=1), lambda m: Model.load(f"{m}model")
)
MODEL_CACHE.start_ttl_thread()

cctx = zstandard.ZstdCompressor(level=10)


def setkey(key: str, value: bytes, compress: bool = False) -> None:
    LOGGER.debug(f"Storing data at {key}: {value!r}")
    if compress:
        value = cctx.compress(value)
    redis.set(key, value)
    redis.expire(key, DEFAULT_EXPIRATION_TTL)


def classify_bug(model_name: str, bug_ids: Sequence[int], bugzilla_token: str) -> str:
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


def classify_issue(
    model_name: str, owner: str, repo: str, issue_nums: Sequence[int]
) -> str:
    from bugbug_http.app import JobInfo

    github = Github(owner=owner, repo=repo)

    issue_ids_set = set(map(int, issue_nums))

    issues = {
        issue_num: github.fetch_issue_by_number(owner, repo, issue_num, True)
        for issue_num in issue_nums
    }

    missing_issues = issue_ids_set.difference(issues.keys())

    for issue_id in missing_issues:
        job = JobInfo(classify_issue, model_name, owner, repo, issue_id)

        # TODO: Find a better error format
        setkey(job.result_key, orjson.dumps({"available": False}))

    if not issues:
        return "NOK"

    model = MODEL_CACHE.get(model_name)

    if not model:
        LOGGER.info("Missing model %r, aborting" % model_name)
        return "NOK"

    model_extra_data = model.get_extra_data()

    # TODO: Classify could choke on a single bug which could make the whole
    # job to fail. What should we do here?
    probs = model.classify(list(issues.values()), True)
    indexes = probs.argmax(axis=-1)
    suggestions = model.le.inverse_transform(indexes)

    probs_list = probs.tolist()
    indexes_list = indexes.tolist()
    suggestions_list = suggestions.tolist()

    for i, issue_id in enumerate(issues.keys()):
        data = {
            "prob": probs_list[i],
            "index": indexes_list[i],
            "class": suggestions_list[i],
            "extra_data": model_extra_data,
        }

        job = JobInfo(classify_issue, model_name, owner, repo, issue_id)
        setkey(job.result_key, orjson.dumps(data), compress=True)

        # Save the bug last change
        setkey(job.change_time_key, issues[issue_id]["updated_at"].encode())

    return "OK"


def classify_broken_site_report(model_name: str, reports_data: list[dict]) -> str:
    from bugbug_http.app import JobInfo

    reports = {
        report["uuid"]: {"title": report["title"], "body": report["body"]}
        for report in reports_data
    }

    if not reports:
        return "NOK"

    model = MODEL_CACHE.get(model_name)

    if not model:
        LOGGER.info("Missing model %r, aborting" % model_name)
        return "NOK"

    model_extra_data = model.get_extra_data()
    probs = model.classify(list(reports.values()), True)
    indexes = probs.argmax(axis=-1)
    suggestions = model.le.inverse_transform(indexes)

    probs_list = probs.tolist()
    indexes_list = indexes.tolist()
    suggestions_list = suggestions.tolist()

    for i, report_uuid in enumerate(reports.keys()):
        data = {
            "prob": probs_list[i],
            "index": indexes_list[i],
            "class": suggestions_list[i],
            "extra_data": model_extra_data,
        }

        job = JobInfo(classify_broken_site_report, model_name, report_uuid)
        setkey(job.result_key, orjson.dumps(data), compress=True)

    return "OK"


@lru_cache(maxsize=None)
def get_known_tasks() -> tuple[str, ...]:
    with open("known_tasks", "r") as f:
        return tuple(line.strip() for line in f)


def schedule_tests(branch: str, rev: str) -> str:
    from bugbug_http import REPO_DIR
    from bugbug_http.app import JobInfo

    job = JobInfo(schedule_tests, branch, rev)
    LOGGER.info("Processing %s...", job)

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

    # On "try", consider commits from other branches too (see https://bugzilla.mozilla.org/show_bug.cgi?id=1790493).
    # On other repos, only consider "default" commits (to exclude commits such as https://hg.mozilla.org/integration/autoland/rev/961f253985a4388008700a6a6fde80f4e17c0b4b).
    if branch == "try":
        repo_branch = None
    else:
        repo_branch = "default"

    data = _analyze_patch(revs, repo_branch)

    setkey(job.result_key, orjson.dumps(data), compress=True)

    return "OK"


def get_config_specific_groups(config: str) -> str:
    from bugbug_http.app import JobInfo

    job = JobInfo(get_config_specific_groups, config)
    LOGGER.info("Processing %s...", job)

    equivalence_sets = testselect._get_equivalence_sets(0.9)

    past_failures_data = test_scheduling.PastFailures("group", True)

    setkey(
        job.result_key,
        orjson.dumps(
            [
                {"name": group}
                for group in past_failures_data.all_runnables
                if any(
                    equivalence_set == {config}
                    for equivalence_set in equivalence_sets[group]
                )
            ]
        ),
        compress=True,
    )

    return "OK"


def schedule_tests_from_patch(base_rev: str, patch_hash: str) -> str:
    from bugbug_http import REPO_DIR
    from bugbug_http.app import JobInfo

    job = JobInfo(schedule_tests_from_patch, base_rev, patch_hash)
    LOGGER.info("Processing %s...", job)

    # Retrieve the patch from Redis
    patch_key = f"bugbug:patch:{patch_hash}"
    patch_data_raw = redis.get(patch_key)

    if not patch_data_raw:
        LOGGER.error(f"Patch not found in Redis for hash {patch_hash}")
        return "NOK"

    hg_base_rev = utils.git2hg(base_rev)
    LOGGER.info(f"Mapped git base rev {base_rev} to hg rev {hg_base_rev}")

    # Pull the base revision to the local repository
    LOGGER.info("Pulling base revision from the remote repository...")
    repository.pull(REPO_DIR, "autoland", hg_base_rev)

    LOGGER.info("Generating commit from patch...")
    commit = repository.generate_commit_from_raw_patch(
        REPO_DIR,
        hg_base_rev,
        patch=patch_data_raw,
        commit_msg="Applied patch for test selection",
    )

    data = _analyze_patch([commit.node.encode("ascii")], "default")

    setkey(job.result_key, orjson.dumps(data), compress=True)

    return "OK"


def _analyze_patch(revs: list[bytes], branch: str | None) -> dict:
    from bugbug_http import REPO_DIR

    commits = repository.download_commits(
        REPO_DIR,
        revs=revs,
        branch=branch,
        save=False,
        use_single_process=True,
        include_no_bug=True,
    )

    if not commits:
        return {
            "tasks": {},
            "groups": {},
            "config_groups": {},
            "reduced_tasks": {},
            "reduced_tasks_higher": {},
            "known_tasks": get_known_tasks(),
        }

    test_selection_threshold = float(
        os.environ.get("TEST_SELECTION_CONFIDENCE_THRESHOLD", 0.5)
    )

    testlabelselect_model = MODEL_CACHE.get("testlabelselect")
    testgroupselect_model = MODEL_CACHE.get("testgroupselect")

    tasks = testlabelselect_model.select_tests(commits, test_selection_threshold)

    reduced = testselect.reduce_configs(
        set(t for t, c in tasks.items() if c >= 0.8), 1.0
    )

    reduced_higher = testselect.reduce_configs(
        set(t for t, c in tasks.items() if c >= 0.9), 1.0
    )

    groups = testgroupselect_model.select_tests(commits, test_selection_threshold)

    config_groups = testselect.select_configs(groups.keys(), 0.9)

    data = {
        "tasks": tasks,
        "groups": groups,
        "config_groups": config_groups,
        "reduced_tasks": {t: c for t, c in tasks.items() if t in reduced},
        "reduced_tasks_higher": {t: c for t, c in tasks.items() if t in reduced_higher},
        "known_tasks": get_known_tasks(),
    }

    return data
