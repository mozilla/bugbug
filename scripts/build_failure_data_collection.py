import csv
import logging
from collections import defaultdict

import requests
import taskcluster
from tqdm import tqdm

from bugbug import bugzilla, db, phabricator, repository

# from libmozdata.hgmozilla import Revision

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


def preprocess_commits_and_bugs():
    logger.info("Preprocessing commits and bugs...")
    bug_commits = {}

    for commit in tqdm(
        repository.get_commits(
            include_no_bug=True, include_backouts=True, include_ignored=True
        )
    ):
        commit_data = {
            key: commit[key]
            for key in ["node", "bug_id", "pushdate", "backedoutby", "backsout"]
        }

        bug_commits.setdefault(commit["bug_id"], []).append(commit_data)

    return bug_commits


def preprocess_revisions():
    logger.info("Preprocessing revisions...")
    diff_id_to_phid = {}

    for revision in phabricator.get_revisions():
        diff_id_to_phid[revision["id"]] = revision["phid"]

    return diff_id_to_phid


def find_bugs(bug_commits, hg_client):
    backed_out_bugs = []

    for bug in bugzilla.get_bugs(include_invalid=True):
        if caused_build_failure(bug["comments"]):
            backing_out_commit = find_backing_out_commit(
                bug_commits.get(bug["id"], None), hg_client
            )
            if not backing_out_commit:
                continue

            commit = {}

            commit["desc"] = hg_client.get_revision(
                "nightly", backing_out_commit["backsout"]
            )["desc"]

            revision_id = repository.get_revision_id(commit)

            backed_out_bugs.append((bug, backing_out_commit, revision_id))

    return backed_out_bugs


def caused_build_failure(comments):
    for comment in comments:
        if (
            "backed out" in comment["text"]
            and "for causing" in comment["text"]
            and "build" in comment["text"]
        ):
            return True
    return False


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

    # 5 get failed tasks
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


def find_backing_out_commit(commits, hg_client):
    if not commits:
        return None

    for commit in commits:
        if not commit["backsout"]:
            continue

        desc = hg_client.get_revision("nightly", commit["node"])["desc"]
        if "backed out" in desc.lower() and "build" in desc.lower():
            return commit
    return None


def main():
    download_databases()

    # bug_commits = preprocess_commits_and_bugs()

    # hg_client = Revision()

    # bugs = find_bugs(bug_commits, hg_client)

    # for bug in bugs:
    #     print(bug[2])

    # backout_revisions = [
    #     27904,
    #     30744,
    #     128537,
    #     127218,
    #     153067,
    #     157855,
    #     161229,
    #     164203,
    #     173115,
    #     174921,
    #     174086,
    #     175742,
    #     20409,
    #     58102,
    #     91663,
    #     205936,
    #     178686,
    #     208953,
    #     211415,
    #     211106,
    #     89590,
    #     214412,
    #     216163,
    #     26390,
    #     219250,
    #     215371,
    # ]

    index = taskcluster.Index(
        {
            "rootUrl": "https://firefox-ci-tc.services.mozilla.com",
            "credentials": {"clientId": "mozilla-auth0/ad|Mozilla-LDAP|bmah"},
        }
    )

    queue = taskcluster.Queue(
        {
            "rootUrl": "https://firefox-ci-tc.services.mozilla.com",
        }
    )

    backout_revisions = [
        153067,
        157855,
        164203,
        178686,
        208953,
        216163,
        215371,
    ]
    revisions_to_commits = defaultdict(list)

    for commit in repository.get_commits():
        revision_id = repository.get_revision_id(commit)

        if revision_id in backout_revisions:
            revisions_to_commits[revision_id].append(commit["node"])

    with open("revisions.csv", mode="w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        writer.writerow(
            ["Revision ID", "Initial Commit", "Fix Commit", "Interdiff", "Error Lines"]
        )

        for revision_id, commits in revisions_to_commits.items():
            commit_diff = repository.get_diff(
                repo_path="hg_dir", original_hash=commits[0], fix_hash=commits[1]
            )
            if not commit_diff:
                continue

            commit_diff_encoded = commit_diff.decode("utf-8")

            error_lines = find_error_lines(index, queue, commits[0])

            writer.writerow(
                [revision_id, commits[0], commits[1], commit_diff_encoded, error_lines]
            )


if __name__ == "__main__":
    main()

    # # ==== experimental purposes ====

    # index = taskcluster.Index(
    #     {
    #         "rootUrl": "https://firefox-ci-tc.services.mozilla.com",
    #         "credentials": {"clientId": "mozilla-auth0/ad|Mozilla-LDAP|bmah"},
    #     }
    # )

    # queue = taskcluster.Queue(
    #     {
    #         "rootUrl": "https://firefox-ci-tc.services.mozilla.com",
    #     }
    # )

    # print(find_error_lines(index, queue, "448597bce69d9e173e0b6818a513b8dfd86f1765"))

    # tc_test = True

    # if tc_test:

    #     index = taskcluster.Index(
    #         {
    #             "rootUrl": "https://firefox-ci-tc.services.mozilla.com",
    #             "credentials": {"clientId": "mozilla-auth0/ad|Mozilla-LDAP|bmah"},
    #         }
    #     )

    #     queue = taskcluster.Queue(
    #         {
    #             "rootUrl": "https://firefox-ci-tc.services.mozilla.com",
    #         }
    #     )

    #     # FINAL STEPS
    #     # 1. list the tasks
    #     # tasks = index.listTasks('gecko.v2.autoland.revision.04d0c38e624dc5fe830b67c0526aafa87d3a63ed.firefox')
    #     commit_node = "448597bce69d9e173e0b6818a513b8dfd86f1765"
    #     commit_node = "04d0c38e624dc5fe830b67c0526aafa87d3a63ed"
    #     commit_node = "759d4948ed8b468dfc03d2ca35e7c8e54b62ae75"
    #     tasks = index.listTasks(f"gecko.v2.autoland.revision.{commit_node}.firefox")
    #     print(tasks)

    #     # 2. get the task ID from one of the tasks (I think any is fine)
    #     first_task_id = tasks["tasks"][0]["taskId"]
    #     print(first_task_id)

    #     # 3. get the task group ID from the task ID
    #     first_task = queue.task(first_task_id)
    #     task_group_id = first_task["taskGroupId"]
    #     print(task_group_id)

    #     # 4. extract the build task IDs from the task group ID
    #     # "https://firefoxci.taskcluster-artifacts.net/YHg9SxOFQiKgWd4Cr9TuRw/0/public/label-to-taskid.json"
    #     url = f"https://firefoxci.taskcluster-artifacts.net/{task_group_id}/0/public/label-to-taskid.json"
    #     response = requests.get(url)
    #     response.raise_for_status()
    #     data = response.json()

    #     build_tasks = set()

    #     for label, taskId in data.items():
    #         if label[:5] == "build":
    #             build_tasks.add(taskId)

    #     # 5 get failed tasks
    #     failed_tasks = set()

    #     for task in queue.listTaskGroup(task_group_id)["tasks"]:
    #         if task["status"]["state"] == "failed":
    #             failed_tasks.add(task["status"]["taskId"])

    #     # 6. find intersection between build tasks and failed tasks
    #     failed_build_tasks = list(build_tasks & failed_tasks)
    #     print(failed_build_tasks)

    #     # 7. get the url to access the log, load it, and extract the ERROR lines
    #     for failed_build_task in failed_build_tasks:
    #         artifact = queue.getArtifact(
    #             taskId=failed_build_task, runId="0", name="public/logs/live.log"
    #         )
    #         print(artifact)
    #         url = artifact["url"]
    #         break

    #     response = requests.get(url)
    #     error_lines = [line for line in response.text.split("\n") if "ERROR - " in line]

    #     for error_line in error_lines:
    #         print(error_line)

    # print(queue.listTaskGroup('04d0c38e624dc5fe830b67c0526aafa87d3a63ed'))
    # print(queue.listTaskGroup('eNsrB5djSb6UZipZi3BPAQ'))

# collect bugs with build failures, along with a list of their revisions X
# identify commit that backs out another commit due to build failure X
# from above, we can also get the node of the commit that caused the backout X
# extract the revision ID from initial commit description X

# ---- Find another commit with the same revision mentioned in the commit description

# use this to associate the commit nodes to the diff IDS --> find the commit before and after the backout -- check diff description?
# or alternatively, check when the reverting change was made (specifically for the build failure backout) --> get the diff before and after this timestamp with a desc with a commit to MOZILLACENTRAL

# once we have the initial and fix patch IDs, we can get the interdiff between them
# we can also get the error message from the initial patch, to find the exact lines
# take a look here https://matrix.to/#/!whDRjjSmICCgrhFHsQ:mozilla.org/$H93f5S5LisVMCEeM2-oB97mHXz6usNAJjWAMUSqQEQc?via=mozilla.org&via=matrix.org&via=braak.pro


# find the commit that happened most recently after the backout --> this is a fix commit
# convert commit id to commit phid (this is a thing in the revision object in phabricator.get_revisions)
# associate commit phid with its revision in phab --> and then associate it with its patch id
# get interdiff between the initial patch and the fix patch
