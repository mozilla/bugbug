import json
import logging
import os
from typing import Any, Dict, Generator, Tuple

from tqdm import tqdm

from bugbug import bugzilla, db, repository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def download_databases() -> None:
    logger.info("Cloning Mercurial database...")
    repository.clone(repo_dir="hg_dir")

    logger.info("Downloading bugs database...")
    assert db.download(bugzilla.BUGS_DB)

    logger.info("Downloading commits database...")
    assert db.download(repository.COMMITS_DB, support_files_too=True)


def preprocess_commits_and_bugs() -> Tuple[Dict, Dict, Dict]:
    logger.info("Preprocessing commits and bugs...")
    commit_dict, bug_to_commit_dict, bug_dict = {}, {}, {}

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

    # We only require the bug's resolution (to check if it is 'FIXED').
    for bug in tqdm(bugzilla.get_bugs(include_invalid=True), desc="Preprocessing bugs"):
        bug_dict[bug.get("id")] = bug["resolution"]

    return commit_dict, bug_to_commit_dict, bug_dict


def generate_datapoints(
    commit_limit: int,
    commit_dict: dict,
    bug_to_commit_dict: dict,
    bug_dict: dict,
    repo_dir: str,
) -> Generator[Dict[str, Any], None, None]:
    counter = 0
    commit_limit = min(commit_limit, 709458)

    logger.info("Generating datapoints...")

    for commit in repository.get_commits(
        include_no_bug=True, include_backouts=True, include_ignored=True
    ):
        bug_info = bug_dict.get(commit["bug_id"])

        counter += 1

        if not commit["backedoutby"] or bug_info != "FIXED":
            continue

        # We only add the commit if it has been backed out and the bug it is for is FIXED.
        fixing_commit, non_backed_out_commits = find_next_commit(
            commit["bug_id"],
            bug_to_commit_dict,
            commit["node"],
            commit["backedoutby"],
        )

        # If the fixing commit could not be found, omit from dataset. Add to a separate file for logging purposes.
        if not fixing_commit:
            yield {
                "non_backed_out_commits": non_backed_out_commits,
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

        commit_diff = repository.get_diff(
            repo_dir, commit["node"], fixing_commit["node"]
        )
        yield {
            "non_backed_out_commits": non_backed_out_commits,
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
            "commit_diff": commit_diff,
        }

        if counter >= commit_limit:
            break


def find_next_commit(
    bug_id: int, bug_to_commit_dict: dict, inducing_node: str, backout_node: str
) -> Tuple[Dict, int]:
    backout_commit_found = False
    fixing_commit = None

    non_backed_out_counter = 0

    for commit in bug_to_commit_dict[bug_id]:
        # If the backout commit has been found in the bug's commit history,
        # find the next commit that has not been backed out or backs out other commits.
        if backout_commit_found:
            if (
                not commit["backedoutby"]
                and not fixing_commit
                and not commit["backsout"]
            ):
                fixing_commit = commit
                non_backed_out_counter += 1
            elif not commit["backedoutby"]:
                non_backed_out_counter += 1

        if commit["node"] == backout_node:
            backout_commit_found = True

    if (
        not fixing_commit
        or fixing_commit["node"] == inducing_node
        or fixing_commit["node"] == backout_node
    ):
        return {}, non_backed_out_counter

    return fixing_commit, non_backed_out_counter


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

    fix_found_counter = 0
    no_fix_found_counter = 0
    backed_out_counter = 0

    with open(dataset_filepath, "w") as file1, open(
        no_fix_commit_filepath, "w"
    ) as file2:
        file1.write("[\n")
        first1 = True

        file2.write("[\n")
        first2 = True

        for item in data_generator:
            if item["non_backed_out_commits"] > 1:
                backed_out_counter += 1

            if item["fix_found"] and item["non_backed_out_commits"] <= 1:
                item.pop("fix_found", None)
                if not first1:
                    file1.write(",\n")
                json_data = json.dumps(item, indent=4)
                file1.write(json_data)
                first1 = False
                fix_found_counter += 1
            elif not item["fix_found"]:
                item.pop("fix_found", None)
                if not first2:
                    file2.write(",\n")
                json_data = json.dumps(item, indent=4)
                file2.write(json_data)
                first2 = False
                no_fix_found_counter += 1

        file1.write("\n]")
        file2.write("\n]")

    logger.info(f"Dataset successfully saved to {dataset_filepath}")
    logger.info(f"Commits without a fix successfully saved to {no_fix_commit_filepath}")

    logger.info(f"Number of commits with fix found saved: {fix_found_counter}")
    logger.info(f"Number of commits with no fix found saved: {no_fix_found_counter}")
    logger.info(
        f"Number of commits with multiple non backed out commits following it: {backed_out_counter}"
    )


def main():
    download_databases()

    commit_dict, bug_to_commit_dict, bug_dict = preprocess_commits_and_bugs()

    data_generator = generate_datapoints(
        commit_limit=10000,
        commit_dict=commit_dict,
        bug_to_commit_dict=bug_to_commit_dict,
        bug_dict=bug_dict,
        repo_dir="hg_dir",
    )

    save_datasets(
        directory_path="dataset",
        dataset_filename="backout_dataset.json",
        no_fix_commit_filename="no_fix_dataset.json",
        data_generator=data_generator,
    )


if __name__ == "__main__":
    main()
