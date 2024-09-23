import argparse
import logging
import os
import re

import orjson
import requests

from bugbug import phabricator
from bugbug.tools.code_review import PhabricatorReviewData
from bugbug.utils import zstd_compress

review_data = PhabricatorReviewData()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class NoDiffsFoundException(Exception):
    def __init__(self, patch_id):
        super().__init__(f"No diffs found for the given patch ID: {patch_id}")
        self.patch_id = patch_id


class NoTransactionsFoundException(Exception):
    def __init__(self, patch_id):
        super().__init__(f"No transactions found for the given patch ID: {patch_id}")
        self.patch_id = patch_id


class NoDiffFoundForPHIDException(Exception):
    def __init__(self, phid):
        super().__init__(f"No diff found for PHID {phid}")
        self.phid = phid


def load_revisions_maps():
    diff_id_to_revision = {}
    diff_phid_to_revision = {}

    for revision in phabricator.get_revisions():
        for transaction in revision["transactions"]:
            if transaction.get("fields", {}).get("diff") is None:
                continue

            diff_id_to_revision[transaction["fields"]["diff"]["id"]] = revision
            diff_phid_to_revision[transaction["fields"]["diff"]["phid"]] = (
                transaction["fields"]["diff"]["id"],
                revision,
            )

    return diff_id_to_revision, diff_phid_to_revision


def find_details_from_revision_phid(phid, revisions_map):
    revision = revisions_map[phid]
    return revision["id"], revision["fields"]["bugzilla.bug-id"]


def find_recent_update(transactions, comment_date_modified):
    updates = [
        transaction
        for transaction in transactions
        if transaction["type"] == "update"
        and transaction["dateModified"] <= comment_date_modified
    ]
    return max(
        updates, key=lambda transaction: transaction["dateModified"], default=None
    )


def fetch_diff_from_url(revision_id, vs_diff_id, fix_patch_id):
    url = f"https://phabricator.services.mozilla.com/D{revision_id}?vs={vs_diff_id}&id={fix_patch_id}&download=true"
    response = requests.get(url)
    if response.status_code == 200:
        return response.text
    else:
        raise Exception(f"Failed to download diff from URL: {url}")


def extract_relevant_diff(patch_diff, filename):
    file_diff_pattern = rf"diff --git a/{re.escape(filename)} b/{re.escape(filename)}\n.*?(?=\ndiff --git|$)"
    match = re.search(file_diff_pattern, patch_diff, re.DOTALL)

    if match:
        return match.group(0)
    else:
        return None


def process_comments(
    limit, diff_length_limit, diff_id_to_revisions_map, diff_phid_to_revisions_map
):
    patch_count = 0

    for patch_id, comments in review_data.get_all_inline_comments(lambda c: True):
        transactions = diff_id_to_revisions_map[patch_id]["transactions"]

        resolved_comments = [comment for comment in comments if comment.is_done]

        if not resolved_comments:
            continue

        for comment in comments:
            comment_date_modified = comment.date_modified
            most_recent_update = find_recent_update(transactions, comment_date_modified)
            if not most_recent_update:
                continue

            fix_patch_id = diff_phid_to_revisions_map.get(
                most_recent_update["fields"].get("new"), [None]
            )[0]

            # If the  most recent patch doesn't exist or is the original patch itself, skip it
            if not fix_patch_id or fix_patch_id == patch_id:
                continue

            revision_info = diff_phid_to_revisions_map.get(
                most_recent_update["fields"].get("new"), [None, {}]
            )[1]
            revision_phid = revision_info.get("phid")
            revision_id = revision_info.get("id")
            bug_id = revision_info.get("fields", {}).get("bugzilla.bug-id")

            try:
                previous_patch_id = diff_phid_to_revisions_map[
                    most_recent_update["fields"]["old"]
                ][0]
            except Exception as e:
                logger.error(f"Failed to find previous patch: {e}")
                continue

            try:
                patch_diff = fetch_diff_from_url(
                    revision_id, previous_patch_id, fix_patch_id
                )
            except Exception as e:
                logger.error(f"Failed to fetch diff: {e}")
                continue

            if len(patch_diff) > diff_length_limit:
                continue

            relevant_diff = extract_relevant_diff(patch_diff, comment.filename)

            if relevant_diff:
                data = {
                    "bug_id": bug_id,
                    "revision_id": revision_id,
                    "revision_phid": revision_phid,
                    "initial_patch_id": patch_id,
                    "fix_patch_id": fix_patch_id,
                    "previous_patch_id": previous_patch_id,
                    "comment": comment.__dict__,
                    "fix_patch_diff": relevant_diff,
                }
                yield data

        patch_count += 1
        if patch_count >= limit:
            break


def main():
    parser = argparse.ArgumentParser(description="Process patch reviews.")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the number of patches to process. No limit if not specified.",
    )
    parser.add_argument(
        "--diff-length-limit",
        type=int,
        default=10000,
        help="Limit the maximum allowed diff length. Default 10000 if not specified.",
    )

    args = parser.parse_args()

    limit = args.limit or float("inf")
    diff_length_limit = args.diff_length_limit or float("inf")

    os.makedirs("patches", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    diff_id_to_revisions_map, diff_phid_to_revisions_map = load_revisions_maps()

    with open(phabricator.FIXED_COMMENTS_DB, "wb") as dataset_file_handle:
        for data in process_comments(
            limit=limit,
            diff_length_limit=diff_length_limit,
            diff_id_to_revisions_map=diff_id_to_revisions_map,
            diff_phid_to_revisions_map=diff_phid_to_revisions_map,
        ):
            dataset_file_handle.write(orjson.dumps(data) + b"\n")

    zstd_compress(phabricator.FIXED_COMMENTS_DB)


if __name__ == "__main__":
    main()
