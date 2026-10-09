# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os
from collections import defaultdict
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
    "componentspecific",
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

TREEHERDER_API_URL = "https://treeherder.mozilla.org/api"
# "expected fail", "intermittent", "infra", "autoclassified intermittent" and
# "intermittent needs bugid".
IGNORED_CLASSIFICATION_IDS = {3, 4, 5, 7, 8}

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
    LOGGER.debug("Storing data at %s: %r", key, value)
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
        LOGGER.info("Missing model %r, aborting", model_name)
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
        LOGGER.info("Missing model %r, aborting", model_name)
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


def classify_comment(
    model_name: str, comment_ids: Sequence[int], bugzilla_token: str
) -> str:
    from bugbug_http.app import JobInfo

    # This should be called in a process worker so it should be safe to set
    # the token here
    comment_ids_set = set(map(int, comment_ids))
    bugzilla.set_token(bugzilla_token)

    comments = bugzilla.get_comments(comment_ids_set)

    missing_comments = comment_ids_set.difference(comments.keys())

    for comment_id in missing_comments:
        job = JobInfo(classify_comment, model_name, comment_id)

        # TODO: Find a better error format
        setkey(job.result_key, orjson.dumps({"available": False}))

    if not comments:
        return "NOK"

    model = MODEL_CACHE.get(model_name)

    if not model:
        LOGGER.info("Missing model %r, aborting", model_name)
        return "NOK"

    model_extra_data = model.get_extra_data()

    probs = model.classify(list(comments.values()), True)
    indexes = probs.argmax(axis=-1)
    suggestions = model.le.inverse_transform(indexes)

    probs_list = probs.tolist()
    indexes_list = indexes.tolist()
    suggestions_list = suggestions.tolist()

    for i, comment_id in enumerate(comments.keys()):
        data = {
            "prob": probs_list[i],
            "index": indexes_list[i],
            "class": suggestions_list[i],
            "extra_data": model_extra_data,
        }

        job = JobInfo(classify_comment, model_name, comment_id)
        setkey(job.result_key, orjson.dumps(data), compress=True)

        bug, _ = comments[comment_id]
        setkey(job.change_time_key, bug["last_change_time"].encode())

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
        LOGGER.info("Missing model %r, aborting", model_name)
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
    repository.pull(REPO_DIR, branch, rev, update=False)

    # Load the full stack of patches leading to that revision
    LOGGER.info("Loading commits to analyze using automationrelevance...")
    try:
        revs = get_hgmo_stack(branch, rev)
    except requests.exceptions.RequestException:
        LOGGER.warning("Push not found for %s @ %s!", branch, rev)
        return "NOK"

    # On "try", consider commits from other branches too (see https://bugzilla.mozilla.org/show_bug.cgi?id=1790493).
    # On other repos, only consider "default" commits (to exclude commits such as https://hg.mozilla.org/integration/autoland/rev/961f253985a4388008700a6a6fde80f4e17c0b4b).
    if branch == "try":
        repo_branch = None
    else:
        repo_branch = "default"

    # On "try", recent failures are specific to each developer's push, so they are
    # not relevant for backouts.
    treeherder_project = None if branch == "try" else branch.split("/")[-1]

    data = _analyze_patch(revs, repo_branch, treeherder_project)

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
        LOGGER.error("Patch not found in Redis for hash %s", patch_hash)
        return "NOK"

    hg_base_rev = utils.git2hg(base_rev)
    LOGGER.info("Mapped git base rev %s to hg rev %s", base_rev, hg_base_rev)

    # Pull the base revision to the local repository
    LOGGER.info("Pulling base revision from the remote repository...")
    repository.pull(REPO_DIR, "integration/autoland", hg_base_rev, update=True)

    LOGGER.info("Generating commit(s) from patch...")
    revs = repository.import_commits(REPO_DIR, hg_base_rev, patch=patch_data_raw)

    data = _analyze_patch(revs, "default")

    setkey(job.result_key, orjson.dumps(data), compress=True)

    return "OK"


def get_recent_failures(project: str, rev: str) -> tuple[set[str], set[str]]:
    """Get the tasks and manifests which failed recently on a Treeherder project.

    Failures classified as intermittent, infra or expected are ignored, and only
    tasks and manifests known to the label and group models are returned.
    """
    session = utils.get_session("treeherder")
    headers = {"User-Agent": utils.get_user_agent()}

    response = session.get(
        f"{TREEHERDER_API_URL}/project/{project}/push/",
        params={
            "count": os.environ.get("BACKOUT_RECENT_PUSHES_COUNT", "20"),
            "tochange": rev,
        },
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    push_revs = {push["id"]: push["revision"] for push in response.json()["results"]}
    if not push_revs:
        return set(), set()

    tasks: set[str] = set()
    task_ids_by_push: dict[int, set[str]] = defaultdict(set)
    url: str | None = f"{TREEHERDER_API_URL}/jobs/"
    params: dict[str, str | int] | None = {
        "push_id__in": ",".join(str(push_id) for push_id in push_revs),
        "result": "testfailed",
        "count": 2000,
    }
    while url is not None:
        response = session.get(url, params=params, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        for values in data["results"]:
            job = dict(zip(data["job_property_names"], values))
            if job["failure_classification_id"] in IGNORED_CLASSIFICATION_IDS:
                continue

            tasks.add(job["job_type_name"])
            task_ids_by_push[job["push_id"]].add(job["task_id"])

        # The "next" URL already contains the query parameters.
        url = data["next"]
        params = None

    groups: set[str] = set()
    for push_id, task_ids in task_ids_by_push.items():
        response = session.get(
            f"{TREEHERDER_API_URL}/project/{project}/push/group_results/",
            params={"revision": push_revs[push_id]},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        group_results = response.json()
        for task_id in task_ids:
            groups.update(
                group for group, ok in group_results.get(task_id, {}).items() if not ok
            )

    known_tasks = set(test_scheduling.PastFailures("label", True).all_runnables)
    known_groups = set(test_scheduling.PastFailures("group", True).all_runnables)

    return (
        {
            task
            for task in map(test_scheduling.rename_task, tasks)
            if task in known_tasks
        },
        {
            group
            for group in (group.split(":")[0] for group in groups)
            if group in known_groups
        },
    )


def _analyze_patch(
    revs: list[bytes], branch: str | None, treeherder_project: str | None = None
) -> dict:
    from bugbug_http import REPO_DIR

    all_commits = repository.download_commits(
        REPO_DIR,
        revs=revs,
        branch=branch,
        save=False,
        include_no_bug=True,
        include_backouts=True,
    )
    commits = [commit for commit in all_commits if not commit["backsout"]]

    # On backouts, select the tasks and manifests which failed recently, as they
    # might have been caused by the backed-out commits.
    backout_tasks: set[str] = set()
    backout_groups: set[str] = set()
    if treeherder_project is not None and len(commits) < len(all_commits):
        try:
            # The last revision is the head of the push.
            backout_tasks, backout_groups = get_recent_failures(
                treeherder_project, revs[-1].decode("ascii")
            )
        except requests.exceptions.RequestException:
            LOGGER.warning("Could not retrieve recent failures from Treeherder")
        else:
            LOGGER.info(
                "Selecting %d tasks and %d manifests which failed recently",
                len(backout_tasks),
                len(backout_groups),
            )

    if not commits and not backout_tasks and not backout_groups:
        return {
            "tasks": {},
            "groups": {},
            "config_groups": {},
            "reduced_tasks": {},
            "reduced_tasks_higher": {},
            "known_tasks": get_known_tasks(),
            "confidence_thresholds": {},
        }

    test_selection_threshold = float(
        os.environ.get("TEST_SELECTION_CONFIDENCE_THRESHOLD", 0.5)
    )

    testlabelselect_model = MODEL_CACHE.get("testlabelselect")
    testgroupselect_model = MODEL_CACHE.get("testgroupselect")

    # The confidence thresholds computed when training the models for the low/medium/high levels
    # used by the taskgraph, as the scale of the confidences depends on the models.
    confidence_thresholds = {
        key: model.confidence_thresholds
        for key, model in (
            ("tasks", testlabelselect_model),
            ("groups", testgroupselect_model),
        )
        if getattr(model, "confidence_thresholds", None)
    }
    tasks_thresholds = confidence_thresholds.get("tasks", {})
    groups_thresholds = confidence_thresholds.get("groups", {})

    known_tasks = get_known_tasks()
    modified_paths = list(set(path for commit in commits for path in commit["files"]))

    tasks = (
        testlabelselect_model.select_tests(
            commits, min([test_selection_threshold, *tasks_thresholds.values()])
        )
        if commits
        else {}
    )
    for task in test_scheduling.find_tasks_for_paths(
        REPO_DIR, known_tasks, modified_paths
    ):
        tasks[task] = 1.0
    for task in backout_tasks:
        tasks[task] = 1.0

    # The recently failing tasks are kept as they are, as the failures might be
    # specific to their configuration.
    reduced = (
        testselect.reduce_configs(
            set(
                t for t, c in tasks.items() if c >= tasks_thresholds.get("medium", 0.8)
            ),
            1.0,
        )
        | backout_tasks
    )

    reduced_higher = (
        testselect.reduce_configs(
            set(t for t, c in tasks.items() if c >= tasks_thresholds.get("high", 0.9)),
            1.0,
        )
        | backout_tasks
    )

    groups = (
        testgroupselect_model.select_tests(
            commits, min([test_selection_threshold, *groups_thresholds.values()])
        )
        if commits
        else {}
    )
    for group in test_scheduling.find_manifests_for_paths(REPO_DIR, modified_paths):
        groups[group] = 1.0
    for group in backout_groups:
        groups[group] = 1.0

    config_groups = testselect.select_configs(groups, 0.9)

    data = {
        "tasks": tasks,
        "groups": groups,
        "config_groups": config_groups,
        "reduced_tasks": {t: c for t, c in tasks.items() if t in reduced},
        "reduced_tasks_higher": {t: c for t, c in tasks.items() if t in reduced_higher},
        "known_tasks": known_tasks,
        "confidence_thresholds": confidence_thresholds,
    }

    return data
