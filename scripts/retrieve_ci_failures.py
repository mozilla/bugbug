# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import os
import subprocess
import tempfile
from collections import defaultdict
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timedelta
from logging import INFO, basicConfig, getLogger
from threading import Lock

import requests
import tenacity
from tqdm import tqdm

from bugbug import db, repository, utils

basicConfig(level=INFO)
logger = getLogger(__name__)

CI_FAILURES_DB = "data/ci_failures.json"
db.register(
    CI_FAILURES_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_ci_failures.latest/artifacts/public/ci_failures.json.zst",
    1,
)

file_locks = {}
primary_lock = Lock()


def get_lock_for_file(path: str) -> Lock:
    with primary_lock:
        if path not in file_locks:
            file_locks[path] = Lock()
        return file_locks[path]


def download_dbs():
    assert db.download(repository.COMMITS_DB)
    db.download(CI_FAILURES_DB)


def query_push_id_by_revision(revision):
    # https://sql.telemetry.mozilla.org/queries/120004/source
    # SELECT MIN(p.id) AS first_push_id
    # FROM push p
    # WHERE p.repository_id = '{{ repository_id }}'
    #   AND p.revision = '{{ revision }}';
    results = utils.query_redash(
        120004,
        {
            "repository_id": 77,
            "revision": revision,
        },
    )
    return results[0]["first_push_id"]


def query_first_push_id_by_date(date):
    # https://sql.telemetry.mozilla.org/queries/119896/source
    # SELECT MIN(p.id) AS first_push_id
    # FROM push p
    # WHERE p.repository_id = '{{ repository_id }}'
    #   AND p.time >= '{{ startdate }}';
    results = utils.query_redash(
        119896,
        {
            "repository_id": 77,
            "startdate": date,
        },
    )
    return results[0]["first_push_id"]


def get_fixed_by_commit_data(first_push_id, last_push_id):
    return utils.query_redash(
        111789,
        {
            "first_push_id": first_push_id,
            "last_push_id": last_push_id,
        },
    )


def get_fixed_by_commit_pushes():
    logger.info("Get previously found failures...")
    fixed_by_commit_pushes = {}
    last_processed_commit = None
    for push in db.read(CI_FAILURES_DB):
        fixed_by_commit_pushes[push["bug_id"]] = {
            "failures": push["failures"],
            "commits": [],
        }

        last_processed_commit = push["failure_commits"][-1]

    # Retrieve the last processed push ID
    if last_processed_commit is not None:
        last_processed_push_id = query_push_id_by_revision(last_processed_commit)
    else:
        last_processed_push_id = 0

    logger.info("Got %d failures.", len(fixed_by_commit_pushes))

    fixed_by_commit_elements = []

    end = datetime.today()
    # Treeherder stores 120 days of data.
    start = end - timedelta(days=120)

    first_push_id = query_first_push_id_by_date(start.strftime("%Y-%m-%d"))
    if first_push_id <= last_processed_push_id:
        first_push_id = last_processed_push_id + 1
    last_push_id = query_first_push_id_by_date(end.strftime("%Y-%m-%d"))

    logger.info(
        "Retrieving 'fixed by commit' data between %d and %d...",
        first_push_id,
        last_push_id,
    )

    MAX_BATCH_SIZE = 210
    MIN_BATCH_SIZE = 1

    current = first_push_id

    with tqdm(total=last_push_id - first_push_id + 1) as pbar:
        while current <= last_push_id:
            batch_size = min(MAX_BATCH_SIZE, last_push_id - current + 1)

            while batch_size >= MIN_BATCH_SIZE:
                first = current
                last = min(current + batch_size - 1, last_push_id)

                try:
                    fixed_by_commit_elements += get_fixed_by_commit_data(first, last)
                except Exception:
                    if batch_size == MIN_BATCH_SIZE:
                        raise

                    batch_size = max(MIN_BATCH_SIZE, batch_size // 2)
                    continue

                processed = last - first + 1
                current = last + 1
                pbar.update(processed)
                break

    fixed_by_commit_elements = [
        element
        for element in fixed_by_commit_elements
        if element["repository_name"] == "autoland"
    ]

    for element in fixed_by_commit_elements:
        if element["bug_id"] is None:
            continue

        bug_id = int(element["bug_id"])

        if bug_id not in fixed_by_commit_pushes:
            fixed_by_commit_pushes[bug_id] = {
                "failures": [],
                "commits": [],
            }

        fixed_by_commit_pushes[bug_id]["failures"].append(
            {
                "task_name": element["job_type_name"],
                "task_id": element["task_id"],
                "retry_id": element["retry_id"],
                "failure_lines": element["failure_lines"],
            }
        )

    logger.info("Analyzing %d 'fixed by commit' pushes.", len(fixed_by_commit_pushes))

    backouts_by_bug_id = defaultdict(int)
    for commit in repository.get_commits(include_backouts=True):
        if commit["bug_id"] not in fixed_by_commit_pushes:
            continue

        fixed_by_commit_pushes[commit["bug_id"]]["commits"].append(commit)

        if commit["backsout"]:
            backouts_by_bug_id[commit["bug_id"]] += 1

    # Skip cases where there is no relanding.
    no_relanding_bugs = set()
    for bug_id, obj in fixed_by_commit_pushes.items():
        if not any(not commit["backedoutby"] for commit in obj["commits"]):
            no_relanding_bugs.add(bug_id)

    logger.info(
        "%s cases removed because there was no relanding.", len(no_relanding_bugs)
    )

    for bug_id in no_relanding_bugs:
        del fixed_by_commit_pushes[bug_id]

    logger.info(
        "%s 'fixed by commit' pushes left to analyze.", len(fixed_by_commit_pushes)
    )

    # Skip cases where there are multiple backouts associated to the same bug ID.
    multiple_backouts = set()
    for bug_id, backouts in backouts_by_bug_id.items():
        if backouts > 1:
            if bug_id in fixed_by_commit_pushes:
                multiple_backouts.add(bug_id)

    logger.info(
        "%s cases to be removed because there were multiple backouts in the same bug.",
        len(multiple_backouts),
    )

    for multiple_backout in multiple_backouts:
        del fixed_by_commit_pushes[multiple_backout]

    logger.info(
        "%s 'fixed by commit' pushes left to analyze.", len(fixed_by_commit_pushes)
    )

    # Skip cases where there is no backout (and so the fix was a bustage fix).
    no_backouts = set()
    for bug_id, obj in fixed_by_commit_pushes.items():
        if bug_id not in backouts_by_bug_id:
            no_backouts.add(bug_id)

        # This is needed because sometimes v-c-t fails to identify backouts.
        elif not any(commit["backedoutby"] for commit in obj["commits"]):
            no_backouts.add(bug_id)

    logger.info(
        "%s cases to be removed because there were no backouts in the bug.",
        len(no_backouts),
    )

    for no_backout in no_backouts:
        del fixed_by_commit_pushes[no_backout]

    logger.info(
        "%s 'fixed by commit' pushes left to analyze.", len(fixed_by_commit_pushes)
    )

    # TODO: skip cases where a single push contains multiple backouts?

    return fixed_by_commit_pushes


def retrieve_logs(fixed_by_commit_pushes, upload):
    os.makedirs(os.path.join("data", "ci_failures_logs"), exist_ok=True)

    all_failures = [
        failure
        for push in fixed_by_commit_pushes.values()
        for failure in push["failures"]
    ]

    cached_keys: set[str] = set()
    if upload:
        logger.info("Listing existing logs in S3...")
        cached_keys = utils.list_s3("data/ci_failures_logs/")

    with ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(process_logs, failure, upload, cached_keys)
            for failure in all_failures
        ]

        # We iterate over the futures as they finish so tqdm can update the progress bar.
        all(tqdm(as_completed(futures), total=len(futures), desc="Retrieving logs"))


def process_logs(failure, upload, cached_keys):
    task_id = failure["task_id"]
    retry_id = failure["retry_id"]

    log_path = os.path.join("data", "ci_failures_logs", f"{task_id}.{retry_id}.log")
    log_zst_path = f"{log_path}.zst"

    with get_lock_for_file(log_zst_path):
        if upload and log_zst_path in cached_keys:
            return

        if os.path.exists(log_path) or os.path.exists(log_zst_path):
            return

        try:
            utils.download_check_etag(
                f"https://firefox-ci-tc.services.mozilla.com/api/queue/v1/task/{task_id}/runs/{retry_id}/artifacts/public/logs/live.log",
                log_path,
            )

            utils.zstd_compress(log_path)

            os.remove(log_path)

            if upload:
                utils.upload_s3([log_zst_path])
        except requests.exceptions.HTTPError:
            pass


def diff_failure_vs_fix(repo, failure_commits, fix_commits):
    try:
        fd, idx = tempfile.mkstemp(prefix="gitidx_")
        os.close(fd)

        try:
            env = dict(os.environ, GIT_INDEX_FILE=idx)
            subprocess.run(
                ["git", "-C", repo, "read-tree", f"{failure_commits[0]}^"],
                env=env,
                check=True,
            )
            for commit in fix_commits:
                patch = subprocess.check_output(
                    ["git", "-C", repo, "show", "--pretty=format:", "-p", commit]
                )
                # Apply the commit's patch into the index (3-way to tolerate drift)
                subprocess.run(
                    ["git", "-C", repo, "apply", "--cached", "--3way"],
                    env=env,
                    check=True,
                    input=patch,
                )
            tree_fixed = (
                subprocess.check_output(["git", "-C", repo, "write-tree"], env=env)
                .decode()
                .strip()
            )
        finally:
            try:
                os.remove(idx)
            except OSError:
                pass

        return subprocess.check_output(
            ["git", "-C", repo, "diff", "-w", failure_commits[-1], tree_fixed]
        )
    except subprocess.CalledProcessError as e:
        logger.error(e)
        return None


def generate_diff_for_bug(bug_id: str, obj: dict, upload: bool, repo_path: str) -> int:
    """Generate and optionally upload the diff for a single bug.

    Returns:
        A tuple of (mapping_errors, diff_errors) where each is 0 or 1
        indicating whether that error type occurred for this bug.
    """
    diff_path = os.path.join("data", "ci_failures_diffs", f"{bug_id}.diff")
    diff_zst_path = f"{diff_path}.zst"

    diff = diff_failure_vs_fix(
        repo_path,
        [
            utils.hg2git(commit["node"])
            for commit in obj["commits"]
            if commit["backedoutby"]
        ],
        [
            utils.hg2git(commit["node"])
            for commit in obj["commits"]
            if not commit["backedoutby"] and not commit["backsout"]
        ],
    )

    if diff is None or len(diff) == 0:
        return 1

    with open(diff_path, "wb") as f:
        f.write(diff)

    utils.zstd_compress(diff_path)

    os.remove(diff_path)

    if upload:
        utils.upload_s3([diff_zst_path])

    return 0


def generate_diffs(repo_url, repo_path, fixed_by_commit_pushes, upload):
    if not os.path.exists(repo_path):
        for attempt in tenacity.Retrying(
            wait=tenacity.wait_exponential(multiplier=2, min=2),
            stop=tenacity.stop_after_attempt(7),
        ):
            with attempt:
                subprocess.run(
                    ["git", "clone", repo_url, repo_path],
                    check=True,
                )

    os.makedirs(os.path.join("data", "ci_failures_diffs"), exist_ok=True)

    cached_keys: set[str] = set()
    if upload:
        logger.info("Listing existing diffs in S3...")
        cached_keys = utils.list_s3("data/ci_failures_diffs/")

    diff_errors = 0
    diffs = []
    for bug_id, obj in tqdm(
        fixed_by_commit_pushes.items(),
        total=len(fixed_by_commit_pushes),
        desc="Generating diffs",
    ):
        diff_path = os.path.join("data", "ci_failures_diffs", f"{bug_id}.diff")
        diff_zst_path = f"{diff_path}.zst"

        if upload and diff_zst_path in cached_keys:
            continue

        if os.path.exists(diff_path) or os.path.exists(diff_zst_path):
            continue

        diffs.append((bug_id, obj))

    with ThreadPoolExecutor() as executor:
        futures = [
            executor.submit(generate_diff_for_bug, bug_id, obj, upload, repo_path)
            for bug_id, obj in diffs
        ]

        for future in tqdm(as_completed(futures), total=len(futures)):
            diff_error = future.result()
            diff_errors += diff_error

    logger.info("Failed generating %s diffs", diff_errors)


def write_results(fixed_by_commit_pushes):
    def results():
        for bug_id, obj in fixed_by_commit_pushes.items():
            yield {
                "bug_id": bug_id,
                "failure_commits": [
                    commit["node"] for commit in obj["commits"] if commit["backedoutby"]
                ],
                "fix_commits": [
                    commit["node"]
                    for commit in obj["commits"]
                    if not commit["backedoutby"] and not commit["backsout"]
                ],
                "failures": obj["failures"],
            }

    db.write(CI_FAILURES_DB, results())
    utils.zstd_compress(CI_FAILURES_DB)


def main() -> None:
    description = (
        "Retrieve CI failures, their logs and generate the diffs that fixed them"
    )
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("repo_url", help="Repository URL.")
    parser.add_argument("repo_path", help="Path to git repository.")
    parser.add_argument("--upload", help="Upload logs and diffs.", action="store_true")
    args = parser.parse_args()

    download_dbs()

    fixed_by_commit_pushes = get_fixed_by_commit_pushes()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(retrieve_logs, fixed_by_commit_pushes, args.upload),
            executor.submit(
                generate_diffs,
                args.repo_url,
                args.repo_path,
                fixed_by_commit_pushes,
                args.upload,
            ),
        )

        done, _ = wait(futures, return_when=ALL_COMPLETED)

        for task in done:
            try:
                _ = task.result()
            except Exception as e:
                raise e

    write_results(fixed_by_commit_pushes)


if __name__ == "__main__":
    main()
