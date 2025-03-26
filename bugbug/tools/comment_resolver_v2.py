import logging

import requests

from bugbug.phabricator import get, set_api_key
from bugbug.tools.code_review import PhabricatorReviewData
from bugbug.utils import get_secret

review_data = PhabricatorReviewData()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
PHABRICATOR_API_URL = "https://phabricator.services.mozilla.com/api/"
PHABRICATOR_API_TOKEN = get_secret("PHABRICATOR_TOKEN")


class CodeGeneratorTool:
    def __init__(
        self,
        client,
        model,
        hunk_size,
    ) -> None:
        self.client = client
        self.model = model
        self.hunk_size = hunk_size

    def run(self, prompt: str):
        response = self.client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model=self.model,
            temperature=0.1,
        )
        return response.choices[0].message.content.strip()

    def get_comment_transaction_from_revision(self, revision_id, comment_id):
        set_api_key(PHABRICATOR_API_URL, PHABRICATOR_API_TOKEN)

        revisions = get(rev_ids=[revision_id])

        if not revisions:
            return None

        for revision in revisions:
            for transaction in revision.get("transactions", []):
                if transaction["type"] == "inline":
                    for comment in transaction.get("comments", []):
                        if comment["id"] == comment_id:
                            return transaction

    def get_changeset_id_for_file(self, diff_id, file_path):
        url = f"{PHABRICATOR_API_URL}differential.diff.search"
        payload = {"api.token": PHABRICATOR_API_TOKEN, "constraints[ids][0]": diff_id}

        response = requests.post(url, data=payload)
        data = response.json()

        if data.get("error_info"):
            raise Exception(f"Error retrieving diff PHID: {data['error_info']}")

        results = data.get("result", {}).get("data", [])
        if not results:
            raise Exception(f"No results found for Diff ID {diff_id}")

        diff_phid = results[0]["phid"]

        url = f"{PHABRICATOR_API_URL}differential.changeset.search"
        changesets = []
        after_cursor = None

        while True:
            payload = {
                "api.token": PHABRICATOR_API_TOKEN,
                "constraints[diffPHIDs][0]": diff_phid,
            }
            if after_cursor:
                payload["after"] = after_cursor

            response = requests.post(url, data=payload)
            data = response.json()

            if data.get("error_info"):
                raise Exception(f"Error retrieving changesets: {data['error_info']}")

            results = data.get("result", {}).get("data", [])
            changesets.extend(results)

            after_cursor = data.get("result", {}).get("cursor", {}).get("after")
            if not after_cursor:
                break

        for changeset in changesets:
            if changeset["fields"]["path"]["displayPath"] == file_path:
                return changeset["id"]

        raise Exception(f"File '{file_path}' not found in Diff {diff_id}")

    def fetch_file_content_from_url(self, changeset_id):
        url = f"https://phabricator.services.mozilla.com/differential/changeset/?view=new&ref={changeset_id}"
        response = requests.get(url)
        response.raise_for_status()
        return response.text

    def generate_prompt_from_raw_file_content(
        self,
        comment_content,
        comment_start_line,
        comment_end_line,
        filepath,
        raw_file_content,
        hunk_size,
    ):
        lines = raw_file_content.splitlines()
        total_lines = len(lines)

        start_line = max(comment_start_line - hunk_size, 1)
        end_line = min(comment_end_line + hunk_size, total_lines)

        snippet_lines = []
        for i in range(start_line, end_line + 1):
            prefix = ""

            # Add markers for the commented section
            if i == comment_start_line:
                prefix = ">>> START COMMENT <<<\n"
            if i == comment_end_line:
                snippet_lines.append(f"{prefix}{i} {lines[i - 1]}\n>>> END COMMENT <<<")
                continue

            snippet_lines.append(f"{prefix}{i} {lines[i - 1]}")

        numbered_snippet = "\n".join(snippet_lines)

        prompt = f"""
You are an expert Firefox software engineer who must modify a Code Snippet based on a given Code Review Comment. The section of the code that the comment refers to is explicitly marked with `>>> START COMMENT <<<` and `>>> END COMMENT <<<` within the snippet.

Instructions:
- The new code changes must be presented in valid Git diff format.
- Lines added should have a `+` prefix.
- Lines removed should have a `-` prefix.
- Remove the line number prefix and the comment markers in your final diff output. They are only there for your reference.
- You are not limited to modifying only the marked section; make any necessary changes to improve the code according to the review comment.
- If the comment is suggesting to either delete or modify a code comment, settle with deleting it unless more context suggests modification.
- Your response must contain changes—do not return an empty diff.
- If the comment spans a singular line, it is most likely referring to the first line (e.g. line 10 to 11, it is most likely referring to line 10).
- Do NOT repeat the prompt or add any extra text.
- Do NOT call functions that don't exist.

Input Details:
Comment Start Line: {comment_start_line}
Comment End Line: {comment_end_line}
Comment File: {filepath}
Code Review Comment: {comment_content}

Code Snippet (with Inline Comment Markers):
{numbered_snippet}

Example Output Format:
--- a/File.cpp
+++ b/File.cpp
@@ -10,7 +10,7 @@
- old line
+ new line

Expected Output Format:
Your response must only contain the following, with no extra text:
(diff output here)
"""

        return prompt

    def ask_llm_if_needs_more_context(
        self,
        comment_content,
        snippet_preview,
    ):
        prompt = f"""
We have the following Code Review Comment:
{comment_content}

Below is a snippet of code we believe might need changes (short hunk):
{snippet_preview}

Question: With this snippet, can you confidently fix the code review comment,
or do you need a larger snippet for more context? You need to be 100% sure you
have ALL the code necessary to fix the comment.

Answer with strictly either YES I CAN FIX or NO I NEED MORE CONTEXT
"""
        answer = self.run(prompt=prompt)
        return answer

    def generate_fix(
        self,
        revision_id,
        diff_id,
        comment_id,
    ):
        transaction = self.get_comment_transaction_from_revision(
            revision_id, comment_id
        )

        filepath = transaction["fields"]["path"]
        comment_start_line = transaction["fields"]["line"]
        comment_end_line = comment_start_line + transaction["fields"]["length"]

        for comment in transaction["comments"]:
            if comment["id"] == comment_id:
                comment_content = comment["content"]["raw"]
                break

        changeset_id = self.get_changeset_id_for_file(diff_id, filepath)
        raw_file_content = self.fetch_file_content_from_url(changeset_id)
        step_size = 10
        max_hunk_size = 100

        while self.hunk_size <= max_hunk_size:
            lines = raw_file_content.splitlines()
            total_lines = len(lines)
            snippet_start = max(comment_start_line - self.hunk_size, 1)
            snippet_end = min(comment_end_line + self.hunk_size, total_lines)
            snippet_preview_lines = lines[snippet_start - 1 : snippet_end]
            snippet_preview = "\n".join(snippet_preview_lines)

            answer = self.ask_llm_if_needs_more_context(
                comment_content=comment_content,
                snippet_preview=snippet_preview,
            ).lower()

            print(f"Answer: {answer}")

            if answer == "yes i can fix":
                break
            elif answer == "no i need more context":
                self.hunk_size += step_size
            else:
                break

        prompt = self.generate_prompt_from_raw_file_content(
            comment_content=comment_content,
            comment_start_line=comment_start_line,
            comment_end_line=comment_end_line,
            filepath=filepath,
            raw_file_content=raw_file_content,
            hunk_size=self.hunk_size,
        )

        generated_fix = self.run(prompt=prompt)
        return generated_fix, prompt
