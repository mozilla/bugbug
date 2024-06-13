# import logging
# import json
# import os
# from bugbug.tools.code_review import PhabricatorReviewData

# # Initialize the Phabricator review data
# review_data = PhabricatorReviewData()

# # Configure logging
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)

# # Create necessary directories if they do not exist
# os.makedirs("patches", exist_ok=True)
# os.makedirs("comments", exist_ok=True)

# # Save comments to a JSON file
# def save_comments_to_file(patch_id, comments):
#     file_path = f"comments/{patch_id}.json"
#     if os.path.exists(file_path):
#         #logger.info(f"Comments file for patch ID {patch_id} already exists.")
#         return

#     with open(file_path, 'w') as f:
#         json.dump([comment.__dict__ for comment in comments], f, indent=4)
#     #logger.info(f"Saved comments for patch ID {patch_id} to {file_path}")

# # Download all patches and retrieve inline comments for the first patch
# def download_and_retrieve_comments():
#     for patch_id, comments in review_data.get_all_inline_comments(lambda c: True):
#         try:
#             review_data.load_patch_by_id(patch_id)
#         except Exception as e:
#             logger.error(f"Failed to load patch {patch_id}: {e}")
#             continue
#         save_comments_to_file(patch_id, comments)

#     return patch_id, comments

# # Get the inline comments
# patch_id, comments = download_and_retrieve_comments()

# --------------------------------------------------------- #
# import json
# from libmozdata.phabricator import PhabricatorAPI

# class PhabricatorClient:
#     def __init__(self, api_key):
#         self.api = PhabricatorAPI(api_key)

#     def find_bug_from_patch(self, patch_id):
#         diffs = self.api.search_diffs(diff_id=patch_id)
#         if not diffs:
#             raise Exception("No diffs found for the given patch ID")
#         revision_phid = diffs[0]['revisionPHID']
#         return self.api.load_revision(rev_phid=revision_phid)

#     def get_transactions_for_revision(self, revision_phid):
#         transactions = self.api.request("transaction.search", objectIdentifier=revision_phid)
#         return transactions['data']

#     def get_full_details(self, patch_id):
#         revision = self.find_bug_from_patch(patch_id)
#         revision_phid = revision['phid']

#         # Fetch all transactions for the revision
#         transactions = self.get_transactions_for_revision(revision_phid)

#         return {
#             'transactions': transactions,
#         }

#     # TODO: 1. given a patch and its inline comments 2. find when the comments were last modified and if it was resolved 3. go through commit history, find the most recent patch 4. apply the original patch to parent of fix, get diff

# --------------------------------------------------------- #

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


# 2. Iterate through comments, get the last time it was modified and go through transaction history and find the patch
def process_comments():
    client = PhabricatorClient()
    comments_dir = "comments"

    for file_name in os.listdir(comments_dir):
        file_path = os.path.join(comments_dir, file_name)
        with open(file_path, "r"):
            patch_id = int(file_name.replace(".json", ""))
            transactions = client.find_transactions_from_patch(patch_id)
            print(json.dumps(transactions, indent=4))
        break


# Example usage
if __name__ == "__main__":
    download_inline_comments()
    process_comments()
