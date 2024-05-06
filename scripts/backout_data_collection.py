import json
import logging
import os

from tqdm import tqdm

from bugbug import bugzilla, db, repository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def download_databases() -> None:
    logger.info("Downloading bugs database...")
    assert db.download(bugzilla.BUGS_DB)

    logger.info("Downloading commits database...")
    assert db.download(repository.COMMITS_DB, support_files_too=True)


def preprocess_commits_and_bugs() -> tuple[dict, dict, dict]:
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

    return commit_dict, bug_to_commit_dict, bug_dict


# def save_dataset(directory_path: str, filename: str, filtered_list: list) -> None:
#     if not os.path.exists(directory_path):
#         os.makedirs(directory_path)
#         logger.info(f"Directory {directory_path} created")

#     json_data = json.dumps(filtered_list, indent=4)

#     with open(directory_path + "/" + filename, "w") as file:
#         file.write(json_data)

#     logger.info(f"Data successfully saved to {directory_path + '/' + filename}")


# def filter_commits(
#     commit_limit: int,
#     commit_dict: dict,
#     bug_to_commit_dict: dict,
#     bug_dict: dict,
# ) -> list:
#     filtered_list = []
#     counter = 0
#     commit_limit = min(commit_limit, 709458)

#     pbar = tqdm(total=commit_limit, desc="Filtering commits")

#     for commit in repository.get_commits(
#         include_no_bug=True, include_backouts=True, include_ignored=True
#     ):
#         # add commit if it was backed out and the bug is fixed
#         bug_info = bug_dict.get(str(commit["bug_id"]))

#         counter += 1
#         pbar.update(1)
#         if commit["backedoutby"] and bug_info == "FIXED":
#             fixing_commit = find_next_commit(
#                 commit["bug_id"], bug_to_commit_dict, commit["node"]
#             )

#             # if fixing commit could not be found or is another backing out commit, do not add it to dataset
#             if (
#                 fixing_commit["node"] == commit["backedoutby"]
#                 or len(fixing_commit["backsout"]) > 0
#             ):
#                 continue

#             # add the hashes of the bug-inducing commit, the back out commit, and the fixing commit
#             # include metadata such as push date and description for further context
#             list_entry = {
#                 "bug_id": commit["bug_id"],
#                 "inducing_commit": {
#                     "node": commit["node"],
#                     "pushdate": commit["pushdate"],
#                     "desc": commit["desc"],
#                 },
#                 "backout_commit": {
#                     "node": commit["backedoutby"],
#                     "pushdate": commit_dict[commit["backedoutby"]]["pushdate"],
#                     "desc": commit_dict[commit["backedoutby"]]["desc"],
#                 },
#                 "fixing_commit": {
#                     "node": fixing_commit["node"],
#                     "pushdate": fixing_commit["pushdate"],
#                     "desc": fixing_commit["desc"],
#                 },
#             }

#             filtered_list.append(list_entry)

#         if counter >= commit_limit:
#             break

#     return filtered_list


def filter_commits(
    commit_limit: int,
    commit_dict: dict,
    bug_to_commit_dict: dict,
    bug_dict: dict,
):
    counter = 0
    commit_limit = min(commit_limit, 709458)
    pbar = tqdm(total=commit_limit, desc="Filtering commits")

    for commit in repository.get_commits(
        include_no_bug=True, include_backouts=True, include_ignored=True
    ):
        # add commit if it was backed out and the bug is fixed
        bug_info = bug_dict.get(commit["bug_id"])

        counter += 1
        pbar.update(1)
        if commit["backedoutby"] and bug_info == "FIXED":
            fixing_commit = find_next_commit(
                commit["bug_id"], bug_to_commit_dict, commit["node"]
            )

            # if fixing commit could not be found or is another backing out commit, do not add it to dataset
            if (
                fixing_commit["node"] == commit["backedoutby"]
                or fixing_commit["backsout"]
            ):
                continue

            # add the hashes of the bug-inducing commit, the back out commit, and the fixing commit
            # include metadata such as push date and description for further context
            yield {
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

    pbar.close()


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


def save_dataset(directory_path: str, filename: str, data_generator):
    if not os.path.exists(directory_path):
        os.makedirs(directory_path)
        logger.info(f"Directory {directory_path} created")

    file_path = os.path.join(directory_path, filename)
    with open(file_path, "w") as file:
        file.write("[\n")
        first = True
        for item in data_generator:
            if not first:
                file.write(",\n")
            json_data = json.dumps(item, indent=4)
            file.write(json_data)
            first = False
        file.write("\n]")

    logger.info(f"Data successfully saved to {file_path}")


def main():
    download_databases()

    commit_dict, bug_to_commit_dict, bug_dict = preprocess_commits_and_bugs()

    data_generator = filter_commits(
        commit_limit=1000000,
        commit_dict=commit_dict,
        bug_to_commit_dict=bug_to_commit_dict,
        bug_dict=bug_dict,
    )

    save_dataset(
        directory_path="dataset",
        filename="backout_dataset.json",
        data_generator=data_generator,
    )


if __name__ == "__main__":
    main()
