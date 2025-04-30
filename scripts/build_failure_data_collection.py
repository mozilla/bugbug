import csv
import logging
import os
from collections import defaultdict
from datetime import datetime

import requests
import taskcluster
from dateutil.relativedelta import relativedelta
from libmozdata.bugzilla import Bugzilla
from libmozdata.hgmozilla import Revision
from tqdm import tqdm

from bugbug import bugzilla, db, phabricator, repository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def download_databases():
    logger.info("Cloning Mercurial database...")
    repository.clone(repo_dir="hg_dir")

    logger.info("Downloading bugs database...")
    assert db.download(bugzilla.BUGS_DB)

    logger.info("Downloading commits database...")
    assert db.download(repository.COMMITS_DB, support_files_too=True)

    logger.info("Downloading revisions database...")
    assert db.download(phabricator.REVISIONS_DB, support_files_too=True)


def get_bz_params():
    fields = ["id"]
    two_years_ago = (datetime.now() - relativedelta(years=2)).strftime("%Y-%m-%d")
    params = {
        "include_fields": fields,
        "f1": "creation_ts",
        "o1": "greaterthan",
        "v1": two_years_ago,
        "f2": "longdesc",
        "o2": "allwords",
        "v2": "backed out causing build",
    }
    return params


def get_backed_out_build_failure_bugs(date="today", bug_ids=[], chunk_size=None):
    params = get_bz_params()
    bugs = {}

    def bug_handler(bug, data):
        data[bug["id"]] = bug

    Bugzilla(
        params,
        bughandler=bug_handler,
        bugdata=bugs,
    ).get_data().wait()

    return bugs


def map_bugs_to_commit(bug_ids):
    logger.info("Mapping bugs to their commits...")
    bug_commits = {}

    for commit in tqdm(
        repository.get_commits(
            include_no_bug=True, include_backouts=True, include_ignored=True
        )
    ):
        if commit["bug_id"] not in bug_ids:
            continue

        commit_data = {
            key: commit[key]
            for key in ["node", "bug_id", "pushdate", "backedoutby", "backsout", "desc"]
        }

        bug_commits.setdefault(commit["bug_id"], []).append(commit_data)

    return bug_commits


def find_bugs(hg_client, bug_ids, bug_commits):
    logger.info("Finding bugs...")
    backed_out_revisions = []

    for bug_id in bug_ids:
        bug_id_commits = bug_commits.get(bug_id, None)
        backing_out_commit = find_backing_out_commit(bug_id_commits, hg_client)

        if not backing_out_commit:
            continue

        logger.info(f"Backing out commit found for bug {bug_id}: {backing_out_commit}")

        commits = [
            {
                "desc": c["desc"],
            }
            for c in bug_id_commits
            if any(
                c["node"].startswith(node) for node in backing_out_commit["backsout"]
            )
        ]

        if commits is None:
            continue

        for commit in commits:
            revision_id = repository.get_revision_id(commit)
            backed_out_revisions.append(revision_id)

    return backed_out_revisions


def find_backing_out_commit(commits, hg_client):
    logger.info("Finding backing out commit...")
    if not commits:
        return None

    backout_commits = [commit for commit in commits if commit["backsout"]]
    if len(backout_commits) > 1:
        logger.info("Multiple backouts detected, skipping this bug.")
        return None

    for commit in commits:
        if not commit["backsout"]:
            continue

        desc = commit["desc"]
        if (
            "backed out" in desc.lower()
            and "for causing" in desc.lower()
            and "build" in desc.lower()
        ):
            return commit
    return None


def find_error_lines(index_client, queue_client, commit_node):
    # FINAL STEPS
    # 1. list the tasks
    tasks = index_client.listTasks(f"gecko.v2.autoland.revision.{commit_node}.firefox")

    if not tasks["tasks"]:
        return []

    # 2. get the task ID from one of the tasks (I think any is fine)
    first_task_id = tasks["tasks"][0]["taskId"]

    # 3. get the task group ID from the task ID
    first_task = queue_client.task(first_task_id)
    task_group_id = first_task["taskGroupId"]

    # 4. extract the build task IDs from the task group ID
    url = f"https://firefoxci.taskcluster-artifacts.net/{task_group_id}/0/public/label-to-taskid.json"
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()

    build_tasks = set()

    for label, taskId in data.items():
        if label[:5] == "build":
            build_tasks.add(taskId)

    # 5. get failed tasks
    failed_tasks = set()

    for task in queue_client.listTaskGroup(task_group_id)["tasks"]:
        if task["status"]["state"] == "failed":
            failed_tasks.add(task["status"]["taskId"])

    # 6. find intersection between build tasks and failed tasks
    failed_build_tasks = list(build_tasks & failed_tasks)

    # 7. get the url to access the log, load it, and extract the ERROR lines
    error_lines = []

    for failed_build_task in failed_build_tasks:
        artifact = queue_client.getArtifact(
            taskId=failed_build_task, runId="0", name="public/logs/live.log"
        )
        url = artifact["url"]

        response = requests.get(url)
        error_lines.extend(
            [line for line in response.text.split("\n") if "ERROR - " in line]
        )

    return error_lines


def main():
    # 0.
    download_databases()

    # 1.
    bugs = get_backed_out_build_failure_bugs()
    bug_ids = list(bugs.keys())

    # 2.
    bug_commits = map_bugs_to_commit(bug_ids)

    # 3.
    hg_client = Revision()
    backed_out_revisions = find_bugs(hg_client, bug_ids, bug_commits)

    # 4.
    revisions_to_commits = defaultdict(list)

    for commit in repository.get_commits():
        revision_id = repository.get_revision_id(commit)

        if revision_id in backed_out_revisions:
            revisions_to_commits[revision_id].append(commit["node"])

    # 5. and 6.

    client_id = os.getenv("TC_CLIENT_ID")

    index = taskcluster.Index(
        {
            "rootUrl": "https://firefox-ci-tc.services.mozilla.com",
            "credentials": {"clientId": client_id},
        }
    )

    queue = taskcluster.Queue(
        {
            "rootUrl": "https://firefox-ci-tc.services.mozilla.com",
        }
    )

    with open("revisions.csv", mode="w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        writer.writerow(
            ["Revision ID", "Initial Commit", "Fix Commit", "Interdiff", "Error Lines"]
        )

        for revision_id, commits in revisions_to_commits.items():
            if len(commits) < 2:
                print("yo")
                continue

            for commit in commits:
                error_lines = find_error_lines(index, queue, commit)

                if error_lines:
                    break

            commit_diff = repository.get_diff(
                repo_path="hg_dir", original_hash=commits[0], fix_hash=commits[-1]
            )

            commit_diff_encoded = commit_diff.decode("utf-8", errors="replace")

            writer.writerow(
                [revision_id, commits[0], commits[1], commit_diff_encoded, error_lines]
            )


if __name__ == "__main__":
    main()

# 0. Download databases
# 1. Identify bugs in Bugzilla that have a backout due to build failures X
# 2. Map only these bugs' commits to the bug ID in a dict
# 3. Find the revision from the bug
# 4. Map the revision to the commits
# 5. Get the interdiff
# 6. Find error lines in the interdiff
