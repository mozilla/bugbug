# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import os
import subprocess
import tempfile
from collections import defaultdict
from concurrent.futures import ALL_COMPLETED, ThreadPoolExecutor, wait
from logging import INFO, basicConfig, getLogger

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


def download_dbs():
    assert db.download(repository.COMMITS_DB)
    db.download(CI_FAILURES_DB)


def get_fixed_by_commit_pushes():
    logger.info("Get previously found failures...")
    fixed_by_commit_pushes = {}
    for push in db.read(CI_FAILURES_DB):
        fixed_by_commit_pushes[push["bug_id"]] = {
            "failures": push["failures"],
            "commits": [],
        }

    r = requests.get(
        "https://sql.telemetry.mozilla.org/api/queries/111789/results.json?api_key={}".format(
            utils.get_secret("REDASH_API_KEY")
        )
    )
    fixed_by_commit_elements = r.json()["query_result"]["data"]["rows"]

    fixed_by_commit_elements = [
        element
        for element in fixed_by_commit_elements
        if element["repository_name"] == "autoland"
    ]

    for element in fixed_by_commit_elements:
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

    logger.info(f"Analyzing {len(fixed_by_commit_pushes)} 'fixed by commit' pushes.")

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
        f"{len(no_relanding_bugs)} cases removed because there was no relanding."
    )

    for bug_id in no_relanding_bugs:
        del fixed_by_commit_pushes[bug_id]

    logger.info(
        f"{len(fixed_by_commit_pushes)} 'fixed by commit' pushes left to analyze."
    )

    # Skip cases where there are multiple backouts associated to the same bug ID.
    multiple_backouts = set()
    for bug_id, backouts in backouts_by_bug_id.items():
        if backouts > 1:
            if bug_id in fixed_by_commit_pushes:
                multiple_backouts.add(bug_id)

    logger.info(
        f"{len(multiple_backouts)} cases to be removed because there were multiple backouts in the same bug."
    )

    for multiple_backout in multiple_backouts:
        del fixed_by_commit_pushes[multiple_backout]

    logger.info(
        f"{len(fixed_by_commit_pushes)} 'fixed by commit' pushes left to analyze."
    )

    # Skip cases where there is no backout (and so the fix was a bustage fix).
    no_backouts = set()
    for bug_id in fixed_by_commit_pushes.keys():
        if bug_id not in backouts_by_bug_id:
            no_backouts.add(bug_id)

    logger.info(
        f"{len(no_backouts)} cases to be removed because there were no backouts in the bug."
    )

    for no_backout in no_backouts:
        del fixed_by_commit_pushes[no_backout]

    logger.info(
        f"{len(fixed_by_commit_pushes)} 'fixed by commit' pushes left to analyze."
    )

    # TODO: skip cases where a single push contains multiple backouts?

    return fixed_by_commit_pushes


def retrieve_logs(fixed_by_commit_pushes, upload):
    os.makedirs(os.path.join("data", "ci_failures_logs"), exist_ok=True)

    for push in tqdm(
        fixed_by_commit_pushes.values(), total=len(fixed_by_commit_pushes)
    ):
        for failure in push["failures"]:
            task_id = failure["task_id"]
            retry_id = failure["retry_id"]

            log_path = os.path.join(
                "data", "ci_failures_logs", f"{task_id}.{retry_id}.log"
            )
            log_zst_path = f"{log_path}.zst"
            if os.path.exists(log_path) or os.path.exists(log_zst_path):
                continue

            if upload and utils.exists_s3(log_path):
                continue

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


def generate_diffs(repo_url, repo_path, fixed_by_commit_pushes, upload):
    if not os.path.exists(repo_path):
        tenacity.retry(
            wait=tenacity.wait_exponential(multiplier=2, min=2),
            stop=tenacity.stop_after_attempt(7),
        )(
            lambda: subprocess.run(
                ["git", "clone", repo_url, repo_path],
                check=True,
            )
        )()

    os.makedirs(os.path.join("data", "ci_failures_diffs"), exist_ok=True)

    diff_errors = 0
    for bug_id, obj in tqdm(
        fixed_by_commit_pushes.items(), total=len(fixed_by_commit_pushes)
    ):
        diff_path = os.path.join("data", "ci_failures_diffs", f"{bug_id}.diff")
        diff_zst_path = f"{diff_path}.zst"
        if os.path.exists(diff_path) or os.path.exists(diff_zst_path):
            continue

        if upload and utils.exists_s3(diff_path):
            continue

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

        if diff is not None and len(diff) > 0:
            with open(diff_path, "wb") as f:
                f.write(diff)

            utils.zstd_compress(diff_path)

            os.remove(diff_path)

            if upload:
                utils.upload_s3([diff_zst_path])
        else:
            diff_errors += 1

    logger.info(f"Failed generating {diff_errors} diffs")


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
                logger.error(f"Task failed with exception {e}")

    write_results(fixed_by_commit_pushes)


if __name__ == "__main__":
    main()
