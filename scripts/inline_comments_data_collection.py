import logging

from bugbug.tools.code_review import PhabricatorReviewData

# Initialize the Phabricator review data
review_data = PhabricatorReviewData()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Download all patches and retrieve inline comments for the first 10 patches
def download_and_retrieve_comments():
    inline_comments = []

    for patch_id, comments in review_data.get_all_inline_comments(lambda c: True):
        review_data.load_patch_by_id(patch_id)
        inline_comments.extend(comments)

    return inline_comments


# Get the inline comments
comments = download_and_retrieve_comments()
