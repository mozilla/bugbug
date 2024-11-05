import logging

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


def find_bugs(bug_commits):
    for bug in bugzilla.get_bugs(include_invalid=True):
        if caused_build_failure(bug["comments"]) and check_backed_out(
            bug_commits.get(bug["id"], None)
        ):
            print(f"BUG: {bug["id"]}")
            print(f"REVISIONS: {bugzilla.get_revision_ids(bug)}")


def caused_build_failure(comments):
    for comment in comments:
        if "backed out" in comment["text"] and "build" in comment["text"]:
            return True
    return False


def check_backed_out(commits):
    if not commits:
        return False

    for commit in commits:
        if commit["backedoutby"]:
            return True
    return False


def load_bug_to_revisions():
    bug_to_revisions = {}

    for revision in phabricator.get_revisions():
        bug_id = revision["fields"].get("bugzilla.bug-id")
        if bug_id is not None:
            if bug_id not in bug_to_revisions:
                bug_to_revisions[bug_id] = []
            bug_to_revisions[bug_id].append(revision)
    return bug_to_revisions


def main():
    download_databases()
    # bug_to_revisions = load_bug_to_revisions()
    bug_commits = preprocess_commits_and_bugs()
    find_bugs(bug_commits)


if __name__ == "__main__":
    main()


# collect bugs with build failures X
# identify the patch that was backed out + caused build failure
# identify the patch that backs it out
# identify the patch after the background
# include the initial diff/patch, the error, and the interdiff between the initial diff and the fix diff
