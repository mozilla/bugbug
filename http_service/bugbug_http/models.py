# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
import os
from datetime import timedelta
from functools import lru_cache
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

import orjson
import requests
import zstandard
from redis import Redis

from bugbug import bugzilla, repository, test_scheduling, utils
from bugbug.github import Github
from bugbug.model import Model
from bugbug.models import get_model_class, testselect
from bugbug.models.perf_regression_predictor import (
    MODEL_IDENTIFIER as PERF_REGRESSION_PREDICTOR,
)
from bugbug.models.perf_regression_predictor import PatchCommitMessageExtractor
from bugbug.utils import get_hgmo_stack, get_hgmo_stack_changesets
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
    "perfregressionpredictor",
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


def load_model(model_name: str) -> Model:
    """Load a model using the implementation registered for its name.

    The base ``Model.load`` unpickles ``model.pkl``, which works for every
    sklearn-based model because the pickle records the concrete class. Models
    stored in other formats (e.g. a Hugging Face checkpoint) override
    ``load``, and that override is only reached when ``load`` is called on
    the registered class rather than on ``Model`` itself.
    """
    return get_model_class(model_name).load(f"{model_name}model")


MODEL_CACHE: ReadthroughTTLCache[str, Model] = ReadthroughTTLCache(
    timedelta(hours=1), load_model
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


# Commits the perf regression predictor must not score, keyed by the reason
# reported to the client. Rules are evaluated in order and the first match
# wins. Each predicate receives a hg.mozilla.org automationrelevance changeset.
PERF_REGRESSION_IGNORED_COMMIT_RULES: dict[str, Callable[[dict], bool]] = {
    # Backouts revert an earlier patch, so their diff mirrors the original
    # change and tells nothing about new regressions.
    "backout": lambda changeset: len(changeset.get("backsoutnodes", [])) > 0,
    # Merges (e.g. mozilla-central into autoland) carry no change of their own.
    "merge": lambda changeset: len(changeset.get("parents", [])) > 1,
    # Automated web-platform-tests imports from upstream.
    "wptsync": lambda changeset: repository.is_wptsync(
        {
            "author_email": changeset.get("author", ""),
            "desc": changeset.get("desc", ""),
        }
    ),
    # Large mechanical changes (e.g. reformatting) excluded from blame.
    "ignore-this-changeset": lambda changeset: (
        "ignore-this-changeset" in changeset.get("desc", "")
    ),
}


def get_perf_regression_ignore_reason(changeset: dict) -> str | None:
    """Return why the perf regression predictor skips a commit, or None to score it."""
    for reason, matches in PERF_REGRESSION_IGNORED_COMMIT_RULES.items():
        if matches(changeset):
            return reason
    return None


def classify_perf_regression(branch: str, rev: str) -> str:
    """Predict performance-regression risk for a push.

    Mirrors :func:`schedule_tests`: the push is resolved server-side against
    the service's own local hg clone. Commits matching one of
    ``PERF_REGRESSION_IGNORED_COMMIT_RULES`` are listed with their
    ``ignored_reason`` but not scored. Every other commit is scored
    separately and the top-level ``risk_score`` is the maximum across the
    scored commits, or ``None`` when every commit in the push was ignored.
    """
    from bugbug_http import REPO_DIR
    from bugbug_http.app import JobInfo

    log_prefix = "[perf-regression-predictor]"

    job = JobInfo(classify_perf_regression, branch, rev)
    LOGGER.info(
        "%s Processing prediction for %s @ %s",
        log_prefix,
        branch,
        rev,
    )

    # Pull the revision to the local repository.
    LOGGER.info(
        "%s Pulling commits from the remote repository...",
        log_prefix,
    )
    repository.pull(REPO_DIR, branch, rev, update=False)

    # Load the full stack of patches leading to that revision.
    LOGGER.info(
        "%s Loading commits to analyze using automationrelevance...",
        log_prefix,
    )
    try:
        changesets = get_hgmo_stack_changesets(branch, rev)
    except requests.exceptions.RequestException:
        LOGGER.warning(
            "%s Push not found for %s @ %s!",
            log_prefix,
            branch,
            rev,
        )
        setkey(job.result_key, orjson.dumps({"available": False}))
        return "OK"

    if not changesets:
        LOGGER.warning(
            "%s No commits to analyze for %s @ %s",
            log_prefix,
            branch,
            rev,
        )
        setkey(job.result_key, orjson.dumps({"available": False}))
        return "OK"

    # Decide which commits to score. Ignored commits are still listed in the
    # result, with the reason they were skipped and no score.
    commits: list[dict[str, Any]] = []
    to_score: list[tuple[int, bytes]] = []
    for changeset in changesets:
        node = changeset["node"]
        ignored_reason = get_perf_regression_ignore_reason(changeset)
        commits.append(
            {
                "node": node,
                "prob": None,
                "class": None,
                "risk_score": None,
                "ignored_reason": ignored_reason,
            }
        )
        if ignored_reason is None:
            to_score.append((len(commits) - 1, node.encode("ascii")))
        else:
            LOGGER.info("%s Ignoring commit %s (%s)", log_prefix, node, ignored_reason)

    # Export each commit to score as its own patch (commit message + diff)
    # from the local clone.
    revs = [node for _, node in to_score]
    patches = repository.get_commit_patches(REPO_DIR, revs) if revs else []

    model = MODEL_CACHE.get(PERF_REGRESSION_PREDICTOR)

    # Score each commit separately. The service runs inference on CPU,
    # so we classify one at a time to keep memory flat.
    extract_commit_message = PatchCommitMessageExtractor()
    for (index, _), patch in zip(to_score, patches):
        patch_text = patch.decode("utf-8", "replace")
        commit_probabilities = model.classify(
            [
                {
                    "commit_message": extract_commit_message(patch_text) or "",
                    "diff": patch_text,
                }
            ],
            probabilities=True,
        )[0]
        commits[index].update(
            {
                "prob": commit_probabilities.tolist(),
                "class": int(commit_probabilities.argmax()),
                "risk_score": float(commit_probabilities[1]),
            }
        )

    scored_risk_scores = [
        commit["risk_score"] for commit in commits if commit["ignored_reason"] is None
    ]
    risk_score = max(scored_risk_scores) if scored_risk_scores else None

    data = {
        "branch": branch,
        "rev": rev,
        "risk_score": risk_score,
        "commits": commits,
        "extra_data": {
            **model.get_extra_data(),
            "commit_count": len(commits),
            "ignored_commit_count": len(commits) - len(scored_risk_scores),
        },
    }
    setkey(job.result_key, orjson.dumps(data), compress=True)
    LOGGER.info(
        "%s Finished prediction for %s @ %s, commit_count=%d, "
        "ignored_commit_count=%d, risk_score=%s",
        log_prefix,
        branch,
        rev,
        len(commits),
        len(commits) - len(scored_risk_scores),
        risk_score,
    )
    return "OK"


# Models that classify a push (branch + revision) rather than a bug or an
# issue, keyed by the model name accepted by the push prediction endpoint.
PUSH_CLASSIFIERS = {
    PERF_REGRESSION_PREDICTOR: classify_perf_regression,
}


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


def _analyze_patch(revs: list[bytes], branch: str | None) -> dict:
    from bugbug_http import REPO_DIR

    commits = repository.download_commits(
        REPO_DIR,
        revs=revs,
        branch=branch,
        save=False,
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

    known_tasks = get_known_tasks()
    modified_paths = list(set(path for commit in commits for path in commit["files"]))

    tasks = testlabelselect_model.select_tests(commits, test_selection_threshold)
    for task in test_scheduling.find_tasks_for_paths(
        REPO_DIR, known_tasks, modified_paths
    ):
        tasks[task] = 1.0

    reduced = testselect.reduce_configs(
        set(t for t, c in tasks.items() if c >= 0.8), 1.0
    )

    reduced_higher = testselect.reduce_configs(
        set(t for t, c in tasks.items() if c >= 0.9), 1.0
    )

    groups = testgroupselect_model.select_tests(commits, test_selection_threshold)
    for group in test_scheduling.find_manifests_for_paths(REPO_DIR, modified_paths):
        groups[group] = 1.0

    config_groups = testselect.select_configs(groups, 0.9)

    data = {
        "tasks": tasks,
        "groups": groups,
        "config_groups": config_groups,
        "reduced_tasks": {t: c for t, c in tasks.items() if t in reduced},
        "reduced_tasks_higher": {t: c for t, c in tasks.items() if t in reduced_higher},
        "known_tasks": known_tasks,
    }

    return data
