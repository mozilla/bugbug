# 1. Retrieve and store all inline comments that have been resolved locally for each patch X
#    make changes to code_review.py to store extra metadata

# 2. Iterate through comments, get the last time it was modified and go through transaction history

# 3. Identify the patch that was landed most recent before the comment was marked as is done, get the diff and store that, also store diff of original patch

# 4. apply cherry picking technique

# 5. store the following in dataset: revision ID, bug ID, initial patch ID, comments, fix patch ID, diff between original and fix patch


import json
import logging
import os

from libmozdata.phabricator import PhabricatorAPI

from bugbug.tools.code_review import PhabricatorReviewData
from bugbug.utils import get_secret

review_data = PhabricatorReviewData()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create necessary directories if they do not exist
os.makedirs("patches", exist_ok=True)
os.makedirs("comments", exist_ok=True)


# Define Phabricator client
class PhabricatorClient:
    def __init__(self):
        self.api = PhabricatorAPI(get_secret("PHABRICATOR_TOKEN"))

    def find_revision_from_patch(self, patch_id):
        diffs = self.api.search_diffs(diff_id=patch_id)

        if not diffs:
            raise Exception(f"No diffs found for the given patch ID: {patch_id}")

        revision_phid = diffs[0]["revisionPHID"]
        return revision_phid

    def find_transactions_from_patch(self, patch_id):
        revision_phid = self.find_revision_from_patch(patch_id)
        transactions = self.api.request(
            "transaction.search", objectIdentifier=revision_phid
        )["data"]

        if not transactions:
            raise Exception(f"No transactions found for the given patch ID: {patch_id}")

        return transactions

    def get_diff_id_from_phid(self, phid):
        diffs = self.api.search_diffs(diff_phid=phid)
        if not diffs:
            raise Exception(f"No diff found for PHID {phid}")
        return diffs[0]["id"]


# 1. Retrieve and store all inline comments that have been resolved locally for each patch X
#    make changes to code_review.py to store extra metadata


def download_inline_comments():
    for patch_id, comments in review_data.get_all_inline_comments(lambda c: True):
        save_comments_to_file(patch_id, comments)
    return


def save_comments_to_file(patch_id, comments):
    resolved_comments = [comment for comment in comments if comment.is_done]

    file_path = f"comments/{patch_id}.json"
    if os.path.exists(file_path) or not resolved_comments:
        return

    with open(file_path, "w") as f:
        json.dump([comment.__dict__ for comment in resolved_comments], f, indent=4)


# 2. Iterate through comments, get the last time it was modified and go through transaction history and find the most recent patch before the comment was resolved
def find_recent_update(transactions, comment_date_modified):
    updates = [
        transaction
        for transaction in transactions
        if transaction["type"] == "update"
        and transaction["dateModified"] <= comment_date_modified
    ]
    if not updates:
        return None
    most_recent_update = max(
        updates, key=lambda transaction: transaction["dateModified"]
    )
    return most_recent_update


# TODO: skip cases where the most recent patch is the original patch itself, so there were no changes made to address the comments
# could be because of accidental comment


def process_comments():
    client = PhabricatorClient()
    comments_dir = "comments"
    count = 0
    for file_name in os.listdir(comments_dir):
        file_path = os.path.join(comments_dir, file_name)
        with open(file_path, "r") as f:
            patch_id = int(file_name.replace(".json", ""))
            transactions = client.find_transactions_from_patch(patch_id)

            comments = json.load(f)
            for comment in comments:
                comment_date_modified = comment["date_modified"]
                most_recent_update = find_recent_update(
                    transactions, comment_date_modified
                )
                if not most_recent_update:
                    continue
                # If the most recent patch is the original patch itself, skip it
                if (
                    client.get_diff_id_from_phid(most_recent_update["fields"]["new"])
                    == patch_id
                ):
                    continue
                print(f"ORIGINAL PATCH >> {patch_id}")
                print(
                    f"FIX PATCH >> {client.get_diff_id_from_phid(most_recent_update['fields']['new'])}"
                )

        count += 1
        if count >= 100:
            break


if __name__ == "__main__":
    download_inline_comments()
    process_comments()
