import sys
import logging
import json
import os
from tqdm import tqdm

sys.path.append('../bugbug')
from bugbug import repository, db, bugzilla

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def download_commits() -> None:
    """
    Download the database of all commits.

    Args:
        None

    Returns:
        None
    """

    logger.info("Downloading commits database...")
    assert db.download(repository.COMMITS_DB, support_files_too=True)

def download_bugs() -> None:
    """
    Download the database of all bugs.

    Args:
        None

    Returns:
        None
    """

    logger.info("Downloading bugs database...")
    assert db.download(bugzilla.BUGS_DB)

def is_bug_fixed(bug_id: int) -> bool:
    """
    Check if a bug (given its ID) is resolved.

    Args:
        bug_id (int): the ID of the bug from Bugzilla

    Returns:
        bool: True if the bug is RESOLVED, False otherwise
    """

    bug_details = bugzilla.get([bug_id])
    bug = bug_details.get(bug_id, {})

    if bug.get("resolution") == 'FIXED':
        return True

    return False

def filter_commits(count_limit: int) -> None:
    """
    Filters the commits based on:
        1. if it is backing out another commit
        2. if the bug is RESOLVED
    Saves to a .json file in ../data/raw_backout_data.json
    
    Args:
        count_limit (int): limit of commits that are added to the json
    
    Returns:
        None

    """
    filtered_list = []
    counter = 0

    pbar = tqdm(total=count_limit, desc="Filtering commits")
    for commit in repository.get_commits(include_no_bug=False, include_backouts=True, include_ignored=False):
        if len(commit["backsout"]) > 0 and is_bug_fixed(commit["bug_id"]):
            filtered_list.append(commit)
            counter += 1
            pbar.update(1)

            if counter >= count_limit:
                break

    json_data = json.dumps(filtered_list, indent=4)
    directory_path = 'data'
    file_path = f'{directory_path}/raw_backout_data.json'

    if not os.path.exists(directory_path):
        os.makedirs(directory_path)
        print(f"Directory {directory_path} created")

    with open(file_path, 'w') as file:
        file.write(json_data)

    logger.info(f"Data successfully saved to {file_path}")

def preprocess_commits():
    """
    Preprocess all commit hashes to create a dictionary mapping the first
    12 characters to the entire hash.

    Args:
        None
    
    Returns:
        commit_dict (dict): dictionary of commit hashes
    
    """
    commit_dict = {}
    for commit in repository.get_commits(include_no_bug=False, include_backouts=True, include_ignored=False):
        shortened_hash = commit['node'][:12]
        commit_dict[shortened_hash] = commit['node']
    return commit_dict


def get_full_hash(shortened_hash: str, commit_dict) -> str:
    """
    Given a shortened hash of 12 characters, search in the commit_dict for the full hash and return it.

    Args:
        shortened_hash (str): 12 character long shortened hash of a commit
        commit_dict (dict): Dictionary of shortened hashes to full hashes
    Returns:
        str: full 40 character long hash of commit, or None if not found
    """

    return commit_dict.get(shortened_hash)

def find_full_hash(source_filepath: str, destination_filepath: str, commit_dict: dict) -> None:
    """
    Convert the first 12 characters of the hash in backsout (list) to the full 40 character long hash using commit_dict.

    Args:
        source_filepath (str): filepath to .json file containing raw backout data
        destination_filepath (str): where to save the new .json file containing the full hashesh
        commit_dict (dict): dictionary mapping shortened hashes to full hashes
    Returns:
        None
    """
    with open(source_filepath, 'r') as file:
        data = json.load(file)

    for commit in data:
        for index, backout_commit in enumerate(commit['backsout']):
            full_hash = get_full_hash(backout_commit, commit_dict)

            if full_hash:
                commit['backsout'][index] = full_hash
    
    with open(destination_filepath, 'w') as file:
        json.dump(data, file, indent=4)
        logger.info(f"Updated data has been saved to {destination_filepath}")
    return data

def clean_dataset(source_filepath: str, destination_filepath: str):
    """
    Given a source filepath to a .json file containing the full hashes, remove
    unnecessary fields and save to destination_filepath.

    Args:
        source_filepath (str): filepath to .json file containing full hash data
        destination_filepath (str): where to save new .json file containing cleaned data
    Returns:
        None
    """

    with open(source_filepath, 'r') as file:
        data = json.load(file)
    
    filtered_data = []

    fields_to_keep = ['node', 'bug_id', 'desc', 'pushdate', 'backsout']

    for commit in data:
        filtered_commit = {key: commit[key] for key in fields_to_keep if key in commit}
        filtered_data.append(filtered_commit)

    with open(destination_filepath, 'w') as file:
        json.dump(filtered_data, file, indent=4)

    logger.info(f"Filtered data has been saved to {destination_filepath}")

    return

if __name__ == "__main__":
    # download commits and bugs
    download_commits()
    download_bugs()

    # filter commits based on backout and are fixed bugs, save to .json
    filter_commits(count_limit=100)
    commit_dict = preprocess_commits()
    find_full_hash(source_filepath="data/raw_backout_data.json", destination_filepath="data/processed_backout_data.json", commit_dict=commit_dict)
    clean_dataset(source_filepath="data/processed_backout_data.json", destination_filepath="data/cleaned_backout_data.json")


