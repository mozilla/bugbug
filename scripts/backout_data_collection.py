import json
import logging
import os
import sys

from tqdm import tqdm

sys.path.append("../bugbug")
from bugbug import bugzilla, db, repository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def download_databases() -> None:
    logger.info("Downloading bugs database...")
    assert db.download(bugzilla.BUGS_DB)

    logger.info("Downloading commits database...")
    assert db.download(repository.COMMITS_DB, support_files_too=True)


def preprocess_commits_and_bugs():
    logger.info("Preprocessing commits")
    commit_dict = {}
    bug_to_commit_dict = {}

    # store commits with their hashes and bug IDs as keys
    for commit in tqdm(
        repository.get_commits(
            include_no_bug=True, include_backouts=True, include_ignored=True
        ),
        desc="Processing commits",
    ):
        commit_dict[commit["node"]] = commit

        if commit["bug_id"] not in bug_to_commit_dict:
            bug_to_commit_dict[commit["bug_id"]] = [commit]
        else:
            bug_to_commit_dict[commit["bug_id"]].append(commit)

    logger.info("Preprocessing bugs")
    bug_dict = {}

    num_lines = sum(1 for line in open(bugzilla.BUGS_DB, "r"))

    # store bugs with their bug IDs as keys
    with open(bugzilla.BUGS_DB, "r") as f:
        for line in tqdm(f, total=num_lines, desc="Processing bugs"):
            bug = json.loads(line)
            bug_dict[bug.get("id")] = bug

    return commit_dict, bug_to_commit_dict, bug_dict


def is_bug_fixed(bug_id: int, bug_dict: dict) -> bool:
    bug = bug_dict.get(bug_id)

    if bug:
        return bug.get("resolution") == "FIXED"

    return False


def filter_commits(
    directory_path: str,
    destination_filepath: str,
    count_limit: int,
    bug_dict: dict,
    commit_dict: dict,
    bug_to_commit_dict: dict,
) -> None:
    filtered_list = []
    counter = 0

    pbar = tqdm(total=count_limit, desc="Filtering commits")

    for commit in repository.get_commits(
        include_no_bug=False, include_backouts=True, include_ignored=False
    ):
        # add commit if it was backed out and the bug is fixed
        if len(commit["backedoutby"]) > 0 and is_bug_fixed(
            bug_id=commit["bug_id"], bug_dict=bug_dict
        ):
            fixing_commit = find_next_commit(
                commit["bug_id"], bug_to_commit_dict, commit["node"]
            )

            # if fixing commit could not be found or is another backing out commit, do not add it to dataset
            if (
                fixing_commit["node"] == commit["backedoutby"]
                or len(fixing_commit["backsout"]) > 0
            ):
                counter += 1
                continue

            # add the hashes of the bug-inducing commit, the back out commit, and the fixing commit
            # include metadata such as push date and description for further context
            list_entry = {
                "bug_id": commit["bug_id"],
                "inducing_commit": {
                    "node": commit["node"],
                    "pushdate": commit["pushdate"],
                    "desc": commit["desc"],
                },
                "backout_commit": {
                    "node": commit["backedoutby"],
                    "pushdate": commit_dict[commit["backedoutby"]]["pushdate"],
                    "desc": commit_dict[commit["backedoutby"]]["desc"],
                },
                "fixing_commit": {
                    "node": fixing_commit["node"],
                    "pushdate": fixing_commit["pushdate"],
                    "desc": fixing_commit["desc"],
                },
            }

            filtered_list.append(list_entry)
            counter += 1
            pbar.update(1)

            if counter >= count_limit:
                break

    json_data = json.dumps(filtered_list, indent=4)

    if not os.path.exists(directory_path):
        os.makedirs(directory_path)
        print(f"Directory {directory_path} created")

    with open(destination_filepath, "w") as file:
        file.write(json_data)

    logger.info(f"Data successfully saved to {destination_filepath}")

    return


def find_next_commit(bug_id: int, bug_to_commit_dict: dict, inducing_node: str) -> dict:
    inducing_commit_found = False
    for commit in bug_to_commit_dict[bug_id]:
        # if the inducing commit has been found, find next commit that has not been backed out
        if inducing_commit_found:
            if len(commit["backedoutby"]) == 0:
                return commit

        if commit["node"] == inducing_node:
            inducing_commit_found = True

    return commit


def main():
    download_databases()

    commit_dict, bug_to_commit_dict, bug_dict = preprocess_commits_and_bugs()

    filter_commits(
        directory_path="dataset",
        destination_filepath="dataset/backout_dataset.json",
        count_limit=500,
        bug_dict=bug_dict,
        commit_dict=commit_dict,
        bug_to_commit_dict=bug_to_commit_dict,
    )


if __name__ == "__main__":
    main()
