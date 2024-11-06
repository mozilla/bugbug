import logging
import re

from libmozdata.hgmozilla import Revision
from tqdm import tqdm

from bugbug import bugzilla, db, phabricator, repository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def download_databases():
    logger.info("Downloading bugs database...")
    assert db.download(bugzilla.BUGS_DB)

    logger.info("Downloading commits database...")
    assert db.download(repository.COMMITS_DB, support_files_too=True)


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
    for bug in bugzilla.get_bugs(include_invalid=True):
        if caused_build_failure(bug["comments"]):
            backing_out_commit = find_backing_out_commit(
                bug_commits.get(bug["id"], None), hg_client
            )
            if not backing_out_commit:
                continue

            print(f"BUG: {bug["id"]}")
            print(f"BACKING OUT COMMIT: {backing_out_commit['node']}")
            print(f"BACKED OUT COMMIT: {backing_out_commit['backsout']}")

            desc = hg_client.get_revision("nightly", backing_out_commit["backsout"])[
                "desc"
            ]
            # print(f"DESCRIPTION OF BACKED OUT COMMIT: {desc}")

            print(f"PHABRICATOR REVISION ID: {extract_revision_id(desc)}")

            # backed_out_bugs.append(bug, backing_out_commit, )


def caused_build_failure(comments):
    for comment in comments:
        if "backed out" in comment["text"] and "build" in comment["text"]:
            return True
    return False


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


def extract_revision_id(desc):
    match = re.search(r"https://phabricator\.services\.mozilla\.com/(D\d+)", desc)
    if match:
        return match.group(1)
    return None


def main():
    download_databases()

    bug_commits = preprocess_commits_and_bugs()
    # rev_id_to_phid = preprocess_revisions()

    hg_client = Revision()

    find_bugs(bug_commits, hg_client)

    # test = Revision()
    # rev = (test.get_revision("nightly", "2e49f991daa3e6b8fb0c1f3ff803ab06b4ec45d6"))
    # if "backed out" in rev["desc"].lower() and "build" in rev["desc"].lower():
    #     print("yes")


if __name__ == "__main__":
    main()

# collect bugs with build failures, along with a list of their revisions X
# identify commit that backs out another commit due to build failure X
# from above, we can also get the node of the commit that caused the backout X
# extract the revision ID from initial commit description
# use this to associate the commit nodes to the diff IDS
# figure out a way to find the fix patch


# once we have the initial and fix patch IDs, we can get the interdiff between them
# we can also get the error message from the initial patch, to find the exact lines ....


# find the commit that happened most recently after the backout --> this is a fix commit
# convert commit id to commit phid (this is a thing in the revision object in phabricator.get_revisions)
# associate commit phid with its revision in phab --> and then associate it with its patch id
# get interdiff between the initial patch and the fix patch
