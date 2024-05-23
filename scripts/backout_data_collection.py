import json
import logging
import os
from collections.abc import Generator
from datetime import datetime, timedelta

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


def preprocess_commits_and_bugs() -> tuple[dict, dict, dict]:
    logger.info("Preprocessing commits and bugs...")
    commit_dict, bug_to_commit_dict, bug_dict = {}, {}, {}

    for commit in repository.get_commits(
        include_no_bug=True, include_backouts=True, include_ignored=True
    ):
        commit_dict[commit["node"]] = {
            "node": commit["node"],
            "bug_id": commit["bug_id"],
            "pushdate": commit["pushdate"],
            "backedoutby": commit["backedoutby"],
            "backsout": commit["backsout"],
        }

        bug_id = commit["bug_id"]
        if bug_id not in bug_to_commit_dict:
            bug_to_commit_dict[bug_id] = [commit_dict[commit["node"]]]
        else:
            bug_to_commit_dict[bug_id].append(commit_dict[commit["node"]])

    # We only require the bug's resolution (to check if it is 'FIXED').
    for bug in bugzilla.get_bugs(include_invalid=True):
        bug_dict[bug.get("id")] = bug["resolution"]

    return commit_dict, bug_to_commit_dict, bug_dict


def has_conflicts(diff: str) -> bool:
    """Return True if the diff contains any conflict markers. Used with merge-tool ':fail'."""
    conflict_markers = ["<<<<<<<", "=======", ">>>>>>>"]
    return any(marker in diff for marker in conflict_markers)


def generate_datapoints(
    commit_limit: int,
    commit_dict: dict,
    bug_to_commit_dict: dict,
    bug_dict: dict,
    repo_dir: str,
) -> Generator[dict, None, None]:
    counter = 0
    commit_limit = min(commit_limit, 709458)

    logger.info("Generating datapoints...")

    for commit in tqdm(
        repository.get_commits(
            include_no_bug=True, include_backouts=True, include_ignored=True
        )
    ):
        counter += 1

        bug_info = bug_dict.get(commit["bug_id"])

        pushdate = datetime.strptime(commit["pushdate"], "%Y-%m-%d %H:%M:%S")

        if (datetime.now() - pushdate) > timedelta(days=730):
            continue

        if not commit["backedoutby"] or bug_info != "FIXED":
            continue

        # We only add the commit if it has been backed out and the bug it is for is FIXED.
        fixing_commit, non_backed_out_commits = find_next_commit(
            commit["bug_id"],
            bug_to_commit_dict,
            commit["node"],
            commit["backedoutby"],
        )

        if not fixing_commit or non_backed_out_commits > 1:
            continue

        commit_diff = repository.get_diff(
            repo_dir, commit["node"], fixing_commit["node"]
        )

        if not commit_diff:
            continue

        commit_diff_encoded = commit_diff.decode("utf-8")

        if has_conflicts(commit_diff_encoded):
            continue

        yield {
            "non_backed_out_commits": non_backed_out_commits,
            "fix_found": True,
            "bug_id": commit["bug_id"],
            "inducing_commit": commit["node"],
            "backout_commit": commit["backedoutby"],
            "fixing_commit": fixing_commit["node"],
            "commit_diff": commit_diff_encoded,
        }

        if counter >= commit_limit:
            break


def find_next_commit(
    bug_id: int, bug_to_commit_dict: dict, inducing_node: str, backout_node: str
) -> tuple[dict, int]:
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
    directory_path: str, dataset_filename: str, data_generator, batch_size: int = 10
) -> None:
    if not os.path.exists(directory_path):
        os.makedirs(directory_path)
        logger.info(f"Directory {directory_path} created")

    dataset_filepath = os.path.join(directory_path, dataset_filename)

    fix_found_counter = 0
    fix_batch = []

    with open(dataset_filepath, "w") as file:
        file.write("[\n")
        first = True

        logger.info("Populating dataset...")
        for item in data_generator:
            item.pop("fix_found", None)
            fix_batch.append(item)
            fix_found_counter += 1

            if len(fix_batch) >= batch_size:
                if not first:
                    file.write(",\n")
                else:
                    first = False

                json_data = ",\n".join(json.dumps(i, indent=4) for i in fix_batch)
                file.write(json_data)
                file.flush()
                os.fsync(file.fileno())
                fix_batch = []

        if fix_batch:
            if not first:
                file.write(",\n")
            json_data = ",\n".join(json.dumps(i, indent=4) for i in fix_batch)
            file.write(json_data)
            file.flush()
            os.fsync(file.fileno())

        file.write("\n]")

    logger.info(f"Dataset successfully saved to {dataset_filepath}")
    logger.info(f"Number of commits with fix found saved: {fix_found_counter}")


def main():
    download_databases()

    commit_dict, bug_to_commit_dict, bug_dict = preprocess_commits_and_bugs()

    data_generator = generate_datapoints(
        commit_limit=1000000,
        commit_dict=commit_dict,
        bug_to_commit_dict=bug_to_commit_dict,
        bug_dict=bug_dict,
        repo_dir="hg_dir",
    )

    save_datasets(
        directory_path="dataset",
        dataset_filename="backout_dataset.json",
        data_generator=data_generator,
        batch_size=1,
    )


if __name__ == "__main__":
    main()
