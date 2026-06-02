# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import re
from concurrent.futures import ThreadPoolExecutor
from logging import INFO, basicConfig, getLogger

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

from bugbug import db, utils

basicConfig(level=INFO)
logger = getLogger(__name__)


CI_FAILURES_DB = "data/ci_failures.json"
db.register(
    CI_FAILURES_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_ci_failures.latest/artifacts/public/ci_failures.json.zst",
    1,
)

TRY_PUSHES_DB = "data/try_pushes.json"
db.register(
    TRY_PUSHES_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_try_pushes.latest/artifacts/public/try_pushes.json.zst",
    1,
)


def download_dbs():
    assert db.download(CI_FAILURES_DB)
    assert db.download(TRY_PUSHES_DB)


try_pushes_by_author: collections.defaultdict[str, list[dict]] = (
    collections.defaultdict(list)
)

TRY_PUSH_METADATA_PREFIXES = (
    "Tasks automatically selected.",
    "Try Chooser Enhanced",
    "Fuzzy query=",
)

SUMMARY_STOP_WORDS = ENGLISH_STOP_WORDS | {"add", "bug", "fix", "remove", "use"}


def _normalize_whitespace(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _summary_line(text):
    if not text:
        return ""

    return _normalize_whitespace(text.splitlines()[0])


def _is_try_metadata_summary(summary):
    return summary.startswith(TRY_PUSH_METADATA_PREFIXES)


def _normalize_summary(summary):
    summary = re.sub(
        r"Differential Revision: https://phabricator\.services\.mozilla\.com/D\d+",
        "",
        summary or "",
    )
    normalized = _normalize_whitespace(summary).lower()
    normalized = re.sub(r"^bug\s+\d+\s*-\s*", "", normalized)
    normalized = re.sub(r"\br[=?][^ ]+\b", "", normalized)

    return _normalize_whitespace(normalized)


def _summary_tokens(summary):
    return {
        token
        for token in re.findall(r"[a-z0-9_]+", summary.lower())
        if len(token) > 2 and token not in SUMMARY_STOP_WORDS and not token.isdigit()
    }


def _extract_differential_revisions(text):
    return {
        match.group(1).upper()
        for match in re.finditer(
            r"Differential Revision: https://phabricator\.services\.mozilla\.com/(D\d+)",
            text or "",
        )
    }


def _build_signature(data):
    signature = {
        "bugs": set(),
        "differential_revisions": set(),
        "files": set(),
        "normalized_summaries": set(),
        "summary_tokens": set(),
        "pushdate": 0,
    }

    for changeset in data.get("changesets", []):
        desc = changeset.get("desc", "")
        summary = _summary_line(desc)

        signature["bugs"].update(
            str(bug["no"]) for bug in changeset.get("bugs", []) if "no" in bug
        )
        signature["differential_revisions"].update(
            _extract_differential_revisions(desc)
        )
        signature["files"].update(
            file_path
            for file_path in changeset.get("files", [])
            if file_path != "try_task_config.json"
        )

        if summary and not _is_try_metadata_summary(summary):
            normalized_summary = _normalize_summary(summary)
            if normalized_summary:
                signature["normalized_summaries"].add(normalized_summary)
                signature["summary_tokens"].update(_summary_tokens(normalized_summary))

        pushdate = changeset.get("pushdate", [0])[0]
        signature["pushdate"] = max(signature["pushdate"], pushdate)

    return signature


def _match(autoland_signature, try_push):
    try_signature = _build_signature(try_push["try_data"])

    autoland_pushdate = autoland_signature["pushdate"]
    try_pushdate = try_signature["pushdate"]
    if autoland_pushdate and try_pushdate and try_pushdate > autoland_pushdate:
        return False, []

    match = False
    reasons = []

    shared_bugs = autoland_signature["bugs"] & try_signature["bugs"]
    if shared_bugs:
        match = True
        reasons.append(f"shared bugs {sorted(shared_bugs)}")

    shared_differential_revisions = (
        autoland_signature["differential_revisions"]
        & try_signature["differential_revisions"]
    )
    if shared_differential_revisions:
        match = True
        reasons.append(
            f"shared differential revisions {sorted(shared_differential_revisions)}"
        )

    shared_summaries = (
        autoland_signature["normalized_summaries"]
        & try_signature["normalized_summaries"]
    )
    if shared_summaries:
        match = True
        reasons.append("shared commit summary")

    shared_files = autoland_signature["files"] & try_signature["files"]
    if autoland_signature["files"] and shared_files:
        file_overlap_ratio = len(shared_files) / len(autoland_signature["files"])

        if len(shared_files) >= 2 and file_overlap_ratio >= 0.9:
            match = True
            reasons.append(
                f"high file overlap ({len(shared_files)}/{len(autoland_signature['files'])})"
            )
        elif len(shared_files) >= 1 and file_overlap_ratio >= 0.5:
            reasons.append(
                f"file overlap ({len(shared_files)}/{len(autoland_signature['files'])})"
            )

    shared_tokens = (
        autoland_signature["summary_tokens"] & try_signature["summary_tokens"]
    )
    if autoland_signature["summary_tokens"] and shared_tokens:
        token_overlap_ratio = len(shared_tokens) / len(
            autoland_signature["summary_tokens"]
        )

        if len(shared_tokens) >= 3 and token_overlap_ratio >= 0.8:
            match = True
            reasons.append("high summary token overlap")
        elif len(shared_tokens) >= 2 and token_overlap_ratio >= 0.5:
            reasons.append("summary token overlap")

    return match, reasons


def match_try_pushes(autoland_rev):
    autoland_data = utils.get_automationrelevance("integration/autoland", autoland_rev)
    author = autoland_data["changesets"][-1]["pushuser"]

    autoland_signature = _build_signature(autoland_data)
    matches = []

    for try_push in try_pushes_by_author[author]:
        match, reasons = _match(autoland_signature, try_push)
        if match:
            matches.append((try_push, reasons))

    if not matches:
        logger.info("No try push match found for %s\n", autoland_rev)
        return []

    logger.info("Matched try pushes for %s:", autoland_rev)
    for try_push, reasons in matches:
        logger.info(
            "%s - %s",
            {try_push["try_data"]["changesets"][-1]["pushhead"]},
            ", ".join(reasons),
        )

    logger.info("\n")

    return [try_push for try_push, reasons in matches]


def main() -> None:
    download_dbs()

    no_try_push = 0
    try_push_with_right_tasks = 0
    try_push_without_right_tasks = 0

    pushes = list(db.read(CI_FAILURES_DB))

    for push in db.read(TRY_PUSHES_DB):
        try_pushes_by_author[push["try_data"]["changesets"][-1]["pushuser"]].append(
            push
        )

    GOOD_MATCHES: list[tuple[str, str]] = [
        (
            "1e4f26666403922d929a4385492c53395918ddbc",
            "c5753d03dd1d1d2da7b28a8dcd13ab76aa2a4dd3",
        ),
    ]

    BAD_MATCHES: list[tuple[str, str]] = []

    matches = []

    with ThreadPoolExecutor() as executor:
        all_try_pushes = executor.map(
            match_try_pushes, (push["failure_commits"][-1] for push in pushes)
        )
        matches = list(zip(pushes, all_try_pushes))

    matched_pairs = {
        (
            push["failure_commits"][-1],
            try_push["try_data"]["changesets"][-1]["pushhead"],
        )
        for push, try_pushes in matches
        for try_push in try_pushes
    }

    for good_match in GOOD_MATCHES:
        assert good_match in matched_pairs, f"Expected good match missing: {good_match}"

    for bad_match in BAD_MATCHES:
        assert bad_match not in matched_pairs, (
            f"Unexpected bad match found: {bad_match}"
        )

    for push, try_pushes in matches:
        if len(try_pushes) == 0:
            no_try_push += 1
            continue

        if any(len(try_push["tasks"]) == 0 for try_push in try_pushes):
            logger.info("At least one push past expiration")
            continue

        autoland_failures = {failure["task_name"] for failure in push["failures"]}
        try_tasks = {
            task["name"] for try_push in try_pushes for task in try_push["tasks"]
        }
        try_passing_tasks = {
            task["name"]
            for try_push in try_pushes
            for task in try_push["tasks"]
            if task["result"] != "failed"
        }

        if len(autoland_failures - try_tasks) > 0:
            logger.info(
                "On %s, the following tasks failed on autoland and were not scheduled on try: %s",
                push["failure_commits"][-1],
                autoland_failures - try_tasks,
            )
            try_push_without_right_tasks += 1

        if len(autoland_failures & try_passing_tasks) > 0:
            logger.info(
                "On %s, the following tasks failed on autoland and passed on try: %s",
                push["failure_commits"][-1],
                autoland_failures & try_passing_tasks,
            )
            try_push_with_right_tasks += 1

    logger.info("%d autoland pushes without a matching try push", no_try_push)
    logger.info(
        "%d autoland pushes with matching try push, where the try push didn't run the right things",
        try_push_without_right_tasks,
    )
    logger.info(
        "%d autoland pushes with matching try push, where the try push run the right things",
        try_push_with_right_tasks,
    )


if __name__ == "__main__":
    main()
