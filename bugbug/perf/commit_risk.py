# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Performance regression risk of commits.

The rules deciding which commits the perf regression predictor never looks at
and the per-patch scoring are shared by the HTTP service endpoint
(``classify_perf_regression`` in ``bugbug_http.models``), the training set
export (``bugbug.perf.regression_commits``) and the batch scorer
(``scripts/perf_commit_risk_scorer.py``), so a kind of commit the model never
scores is not trained on either. Backouts are not among them: a revert is a
real code change and regresses benchmarks like any other, so they are scored
and trained on. The scorer runs the model once over every commit of every push
in the pushes snapshot and stores the scores in ``perf_commit_risk.json``, so
evaluations replaying scheduling strategies over history read scores instead
of calling the endpoint.
"""

import collections
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator

import hglib

from bugbug import repository
from bugbug.perf.regression_labeling import is_within_window
from bugbug.utils import utcnow

logger = logging.getLogger(__name__)

# Keyed by the reason reported with the result. Rules are evaluated in order
# and the first match wins. Each predicate receives a changeset shaped like an
# hg.mozilla.org automationrelevance changeset (``node``, ``desc``, ``author``,
# ``parents``); ``changesets_from_clone`` and ``changeset_from_export`` build
# the same shape from a local clone and from an ``hg export`` payload.
IGNORED_COMMIT_RULES: dict[str, Callable[[dict[str, Any]], bool]] = {
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


def get_ignore_reason(changeset: dict[str, Any]) -> str | None:
    """Return why the perf regression predictor skips a commit, or None to score it."""
    for reason, matches in IGNORED_COMMIT_RULES.items():
        if matches(changeset):
            return reason
    return None


def changeset_from_export(export: repository.HgExport) -> dict[str, Any]:
    """Build the changeset fields the rules read from a parsed ``hg export``."""
    return {
        "author": export.author,
        "parents": export.parents,
        "desc": export.message,
    }


def score_patch(model: Any, patch: bytes) -> dict[str, Any]:
    """Score one ``hg export`` patch with the perf regression predictor.

    Returns the class probabilities, the predicted class and the risk score,
    which is the probability of the positive class. The message and the diff
    are split from the export the way the training set stores them; the model
    truncates the diff to its context window itself.
    """
    export = repository.parse_hg_export(patch)
    probabilities = model.classify(
        [{"commit_message": export.message, "diff": export.diff}],
        probabilities=True,
    )[0]
    return {
        "prob": probabilities.tolist(),
        "class": int(probabilities.argmax()),
        "risk_score": float(probabilities[1]),
    }


LOG_FIELDS = ("node", "author", "desc", "p1node", "p2node")
LOG_TEMPLATE = "".join(f"{{{field}}}\\0" for field in LOG_FIELDS)

# Mercurial's hash for "no commit", which {p1node} and {p2node} render when a
# parent slot is empty.
NULL_NODE = "0" * 40


def changesets_from_clone(
    hg: hglib.client.hgclient, nodes: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Describe commits of a local clone like automationrelevance does.

    Returns a changeset per node found, keyed by node, with ``node``, ``desc``,
    ``author`` and ``parents``. Nodes the clone does not have are left out
    rather than failing the whole batch.
    """
    nodes = list(nodes)
    if not nodes:
        return {}
    revset = "+".join(f"present({node})" for node in nodes)
    args = hglib.util.cmdbuilder(b"log", template=LOG_TEMPLATE, rev=revset)
    values = [
        value.decode("utf-8", "replace") for value in hg.rawcommand(args).split(b"\x00")
    ]
    changesets = {}
    for i in range(0, len(values) - 1, len(LOG_FIELDS)):
        node, author, desc, p1node, p2node = values[i : i + len(LOG_FIELDS)]
        parents = [p1node] if p1node != NULL_NODE else []
        if p2node != NULL_NODE:
            parents.append(p2node)
        changesets[node] = {
            "node": node,
            "desc": desc,
            "author": author,
            "parents": parents,
        }
    return changesets


def is_current_record(record: dict[str, Any], model_version: str) -> bool:
    """Whether a ``perf_commit_risk.json`` record still stands for ``model_version``.

    A record skipped for a reason that is no longer a rule, such as the backout
    rule the predictor dropped, is stale: its commit is scored again and the
    new record supersedes it.
    """
    return record["model_version"] == model_version and (
        record["ignored_reason"] is None
        or record["ignored_reason"] in IGNORED_COMMIT_RULES
    )


def select_unscored_commits(
    pushes: Iterable[dict[str, Any]],
    scored: set[str],
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    """Commits of the pushes in ``[since, until)`` whose node is not in ``scored``.

    Returned in push order, each with the push fields a score record carries:
    ``node``, ``push_id``, ``pushlog_id`` and ``push_time``.
    """
    end = until if until is not None else datetime.max.replace(tzinfo=timezone.utc)
    # Reduced to the needed fields before sorting, so the snapshot's job data
    # is never held in memory.
    selected = [
        (
            push["time"],
            push["push_id"],
            push.get("pushlog_id"),
            [commit["revision"] for commit in push["commits"]],
        )
        for push in pushes
        if is_within_window(push["time"], since, end)
    ]
    selected.sort(key=lambda item: (item[0], item[1]))
    commits = []
    for push_time, push_id, pushlog_id, nodes in selected:
        for node in nodes:
            if node in scored:
                continue
            commits.append(
                {
                    "node": node,
                    "push_id": push_id,
                    "pushlog_id": pushlog_id,
                    "push_time": push_time,
                }
            )
    return commits


def score_commits(
    model: Any,
    model_version: str,
    repo_dir: str,
    commits: list[dict[str, Any]],
    chunk_size: int = 50,
    deadline: float | None = None,
    stats: collections.Counter | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield one ``perf_commit_risk.json`` record per commit found in ``repo_dir``.

    Commits are described ``chunk_size`` at a time with one ``hg log``, then
    exported and scored one by one so a single patch is in memory at a time.
    Ignored commits get their reason and null scores. Commits missing from
    the clone, such as ones not yet merged to mozilla-central, are skipped and
    counted in ``stats`` so a later run scores them. Stops cleanly once
    ``time.monotonic()`` passes ``deadline``.
    """
    stats = stats if stats is not None else collections.Counter()
    scored_at = utcnow().isoformat()
    with hglib.open(repo_dir) as hg:
        for start in range(0, len(commits), chunk_size):
            if start and start % (chunk_size * 20) == 0:
                logger.info(
                    "%d of %d commits done: %d scored, %d ignored, %d missing",
                    start,
                    len(commits),
                    stats["scored"],
                    stats["ignored"],
                    stats["missing"],
                )
            chunk = commits[start : start + chunk_size]
            changesets = changesets_from_clone(hg, (c["node"] for c in chunk))
            for commit in chunk:
                if deadline is not None and time.monotonic() >= deadline:
                    logger.info("Time budget exhausted, stopping")
                    stats["unscored"] += len(commits) - start - chunk.index(commit)
                    return
                changeset = changesets.get(commit["node"])
                if changeset is None:
                    stats["missing"] += 1
                    continue
                record = {
                    **commit,
                    "ignored_reason": get_ignore_reason(changeset),
                    "prob": None,
                    "class": None,
                    "risk_score": None,
                    "model_version": model_version,
                    "scored_at": scored_at,
                }
                if record["ignored_reason"] is None:
                    patch = repository.get_commit_patch(
                        hg, commit["node"].encode("ascii")
                    )
                    record.update(score_patch(model, patch))
                    stats["scored"] += 1
                else:
                    stats["ignored"] += 1
                yield record


def load_model(model_dir: str | None = None) -> Any:
    """Load the perf regression predictor from ``model_dir``.

    Without a directory, the published checkpoint is downloaded into the
    model's default directory under the current directory, unless an
    up-to-date copy is already there.
    """
    from bugbug import utils
    from bugbug.models.perf_regression_predictor import (
        MODEL_IDENTIFIER,
        PerfRegressionPredictorModel,
    )

    if model_dir is None:
        model_dir = utils.download_model(MODEL_IDENTIFIER)
    return PerfRegressionPredictorModel.load(model_dir)


def get_model_version(model: Any) -> str:
    """The version the model reports, recorded with every score it produces."""
    version = model.get_extra_data().get("model_version")
    if not version:
        raise ValueError("The model reports no version")
    return str(version)
