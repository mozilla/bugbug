import json
import logging
import os
from typing import Any, Dict, Generator, Tuple

from tqdm import tqdm

from bugbug import bugzilla, db, repository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def download_databases() -> None:
    logger.info("Downloading bugs database...")
    assert db.download(bugzilla.BUGS_DB)

    logger.info("Downloading commits database...")
    assert db.download(repository.COMMITS_DB, support_files_too=True)


def preprocess_commits_and_bugs() -> Tuple[Dict, Dict, Dict]:
    logger.info("Preprocessing commits and bugs...")
    commit_dict = {}
    bug_to_commit_dict = {}

    # store commits with their hashes and bug IDs as keys
    for commit in tqdm(
        repository.get_commits(
            include_no_bug=True, include_backouts=True, include_ignored=True
        ),
        desc="Preprocessing commits",
    ):
        commit_dict[commit["node"]] = {
            "node": commit["node"],
            "bug_id": commit["bug_id"],
            "desc": commit["desc"],
            "pushdate": commit["pushdate"],
            "backedoutby": commit["backedoutby"],
            "backsout": commit["backsout"],
        }

        if commit_dict[commit["node"]]["bug_id"] not in bug_to_commit_dict:
            bug_to_commit_dict[commit["bug_id"]] = [commit_dict[commit["node"]]]
        else:
            bug_to_commit_dict[commit["bug_id"]].append(commit_dict[commit["node"]])

    logger.info("Preprocessing bugs")
    bug_dict = {}

    # store bugs with their bug IDs as keys
    for bug in tqdm(bugzilla.get_bugs(include_invalid=True), desc="Preprocessing bugs"):
        bug_dict[bug.get("id")] = bug["resolution"]

    return commit_dict, bug_to_commit_dict, bug_dict


def filter_commits(
    commit_limit: int,
    commit_dict: dict,
    bug_to_commit_dict: dict,
    bug_dict: dict,
) -> Generator[Dict[str, Any], None, None]:
    counter = 0
    commit_limit = min(commit_limit, 709458)
    logger.info("Filtering commits...")

    for commit in repository.get_commits(
        include_no_bug=True, include_backouts=True, include_ignored=True
    ):
        bug_info = bug_dict.get(commit["bug_id"])

        counter += 1

        # add commit if it was backed out and the bug is fixed
        if commit["backedoutby"] and bug_info == "FIXED":
            fixing_commit = find_next_commit(
                commit["bug_id"],
                bug_to_commit_dict,
                commit["node"],
                commit["backedoutby"],
            )

            # if fixing commit could not be found, do not add to the dataset
            # instead, will log and add to separate file
            if not fixing_commit:
                yield {
                    "fix_found": False,
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
                }
                continue

            # generate the hashes of the bug-inducing commit, the backout commit, and the fixing commit
            # include metadata such as push date and description for further context
            yield {
                "fix_found": True,
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

        if counter >= commit_limit:
            break


def find_next_commit(
    bug_id: int, bug_to_commit_dict: dict, inducing_node: str, backout_node: str
) -> Dict:
    backout_commit_found = False
    fixing_commit = None

    for commit in bug_to_commit_dict[bug_id]:
        # if the backout commit is found, find the next commit that isn't backed out by any other commit
        if backout_commit_found:
            if not commit["backedoutby"]:
                fixing_commit = commit
                break

        if commit["node"] == backout_node:
            backout_commit_found = True

    if (
        not fixing_commit
        or fixing_commit["node"] == inducing_node
        or fixing_commit["node"] == backout_node
    ):
        return {}

    return fixing_commit


def save_datasets(
    directory_path: str,
    dataset_filename: str,
    no_fix_commit_filename: str,
    data_generator,
) -> None:
    if not os.path.exists(directory_path):
        os.makedirs(directory_path)
        logger.info(f"Directory {directory_path} created")

    dataset_filepath = os.path.join(directory_path, dataset_filename)
    no_fix_commit_filepath = os.path.join(directory_path, no_fix_commit_filename)

    with open(dataset_filepath, "w") as file1, open(
        no_fix_commit_filepath, "w"
    ) as file2:
        file1.write("[\n")
        first1 = True

        file2.write("[\n")
        first2 = True

        for item in data_generator:
            if item["fix_found"]:
                item.pop("fix_found", None)
                if not first1:
                    file1.write(",\n")
                json_data = json.dumps(item, indent=4)
                file1.write(json_data)
                first1 = False
            else:
                item.pop("fix_found", None)
                if not first2:
                    file2.write(",\n")
                json_data = json.dumps(item, indent=4)
                file2.write(json_data)
                first2 = False

        file1.write("\n]")
        file2.write("\n]")

    logger.info(f"Dataset successfully saved to {dataset_filepath}")
    logger.info(f"Commits without a fix successfully saved to {no_fix_commit_filepath}")


def main():
    download_databases()

    commit_dict, bug_to_commit_dict, bug_dict = preprocess_commits_and_bugs()

    data_generator = filter_commits(
        commit_limit=1000000,
        commit_dict=commit_dict,
        bug_to_commit_dict=bug_to_commit_dict,
        bug_dict=bug_dict,
    )

    save_datasets(
        directory_path="dataset",
        dataset_filename="backout_dataset.json",
        no_fix_commit_filename="no_fix_dataset.json",
        data_generator=data_generator,
    )


if __name__ == "__main__":
    main()
