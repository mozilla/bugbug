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


def save_dict_to_file(data_dict, file_path) -> None:
    with open(file_path, "w") as file:
        json.dump(data_dict, file, indent=4)


def load_dict_from_file(file_path) -> dict:
    with open(file_path, "r") as file:
        return json.load(file)


def preprocess_commits_and_bugs(
    directory_path: str,
    commit_cache_filename: str,
    bug_to_commit_cache_filename: str,
    bug_cache_filename: str,
) -> tuple[dict, dict, dict]:
    commit_cache_filepath = directory_path + "/" + commit_cache_filename
    bug_to_commit_cache_filepath = directory_path + "/" + bug_to_commit_cache_filename
    bug_cache_filepath = directory_path + "/" + bug_cache_filename

    if (
        os.path.exists(commit_cache_filepath)
        and os.path.exists(bug_to_commit_cache_filepath)
        and os.path.exists(bug_cache_filepath)
    ):
        logger.info("Loading cached data...")
        commit_dict = load_dict_from_file(commit_cache_filepath)
        bug_to_commit_dict = load_dict_from_file(bug_to_commit_cache_filepath)
        bug_dict = load_dict_from_file(bug_cache_filepath)

    else:
        logger.info("Preprocessing commits and bugs...")
        commit_dict = {}
        bug_to_commit_dict = {}

        # store commits with their hashes and bug IDs as keys
        for commit in tqdm(
            repository.get_commits(
                include_no_bug=True, include_backouts=True, include_ignored=True
            ),
            desc="Processing commits",
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

        num_lines = sum(1 for line in open(bugzilla.BUGS_DB, "r"))

        # store bugs with their bug IDs as keys
        with open(bugzilla.BUGS_DB, "r") as f:
            for line in tqdm(f, total=num_lines, desc="Processing bugs"):
                bug = json.loads(line)
                bug_dict[bug.get("id")] = bug["resolution"]

        save_dict_to_file(commit_dict, commit_cache_filepath)
        save_dict_to_file(bug_to_commit_dict, bug_to_commit_cache_filepath)
        save_dict_to_file(bug_dict, bug_cache_filepath)

    return commit_dict, bug_to_commit_dict, bug_dict


def ensure_directory_exists(directory_path: str) -> None:
    if not os.path.exists(directory_path):
        os.makedirs(directory_path)
        logger.info(f"Directory {directory_path} created")


def save_dataset(directory_path: str, filename: str, filtered_list: list) -> None:
    json_data = json.dumps(filtered_list, indent=4)

    with open(directory_path + "/" + filename, "w") as file:
        file.write(json_data)

    logger.info(f"Data successfully saved to {directory_path + '/' + filename}")


def filter_commits(
    commit_limit: int,
    commit_dict: dict,
    bug_to_commit_dict: dict,
    bug_dict: dict,
) -> list:
    filtered_list = []
    counter = 0
    commit_limit = min(commit_limit, 709458)

    pbar = tqdm(total=commit_limit, desc="Filtering commits")

    for commit in repository.get_commits(
        include_no_bug=True, include_backouts=True, include_ignored=True
    ):
        # add commit if it was backed out and the bug is fixed
        bug_info = bug_dict.get(str(commit["bug_id"]))

        counter += 1
        pbar.update(1)
        if commit["backedoutby"] and bug_info == "FIXED":
            fixing_commit = find_next_commit(
                commit["bug_id"], bug_to_commit_dict, commit["node"]
            )

            # if fixing commit could not be found or is another backing out commit, do not add it to dataset
            if (
                fixing_commit["node"] == commit["backedoutby"]
                or len(fixing_commit["backsout"]) > 0
            ):
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

        if counter >= commit_limit:
            break

    return filtered_list


def find_next_commit(bug_id: int, bug_to_commit_dict: dict, inducing_node: str) -> dict:
    inducing_commit_found = False
    for commit in bug_to_commit_dict[str(bug_id)]:
        # if the inducing commit has been found, find next commit that has not been backed out
        if inducing_commit_found:
            if len(commit["backedoutby"]) == 0:
                return commit

        if commit["node"] == inducing_node:
            inducing_commit_found = True

    return commit


DIRECTORY = "dataset"


def main():
    download_databases()

    ensure_directory_exists(directory_path=DIRECTORY)

    commit_dict, bug_to_commit_dict, bug_dict = preprocess_commits_and_bugs(
        directory_path=DIRECTORY,
        commit_cache_filename="commit_cache.json",
        bug_to_commit_cache_filename="bug_to_commit_cache.json",
        bug_cache_filename="bug_cache.json",
    )

    filtered_list = filter_commits(
        commit_limit=1000000,
        commit_dict=commit_dict,
        bug_to_commit_dict=bug_to_commit_dict,
        bug_dict=bug_dict,
    )

    save_dataset(
        directory_path=DIRECTORY,
        filename="backout_dataset.json",
        filtered_list=filtered_list,
    )


if __name__ == "__main__":
    main()
