import logging

import requests
from langchain.chains import LLMChain
from langchain.prompts import (
    PromptTemplate,
)

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
        llm,
    ) -> None:
        self.client = client
        self.model = model
        self.hunk_size = hunk_size
        self.default_hunk_size = hunk_size
        self.llm = llm
        self.actionability_prompt_template = PromptTemplate(
            input_variables=["comment", "code"],
            template="""Given the following code and a reviewer comment, determine if the comment is actionable.

An actionable comment is one that:
- Clearly requests a change in the code.
- Does not require external actions (e.g. filing a bug).
- Is not just pointing something out without asking for changes.
- Is not too vague or unclear to act on.

Respond with only YES or NO.

Comment:
{comment}

Code:
{code}
""",
        )

        self.actionability_chain = LLMChain(
            llm=self.llm,
            prompt=self.actionability_prompt_template,
        )

        self.generate_fix_prompt_template = PromptTemplate(
            input_variables=[
                "comment_start_line",
                "comment_end_line",
                "filepath",
                "comment_content",
                "numbered_snippet",
            ],
            template="""You are an expert Firefox software engineer who must modify a Code Snippet based on a given Code Review Comment. The section of the code that the comment refers to is explicitly marked with `>>> START COMMENT <<<` and `>>> END COMMENT <<<` within the snippet.

Instructions:
- The new code changes must be presented in valid Git diff format.
- Lines added should have a `+` prefix.
- Lines removed should have a `-` prefix.
- Lines that are modified should have two lines, one with `-` and one with `+` prefix.
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
""",
        )
        self.generate_fix_chain = LLMChain(
            llm=self.llm,
            prompt=self.generate_fix_prompt_template,
        )

        self.more_context_prompt_template = PromptTemplate(
            input_variables=["comment_content", "snippet_preview"],
            template="""We have the following Code Review Comment:
{comment_content}

Below is a snippet of code we believe might need changes (short hunk):
{snippet_preview}

Question: With this snippet, can you confidently fix the code review comment,
or do you need a larger snippet for more context? You need to be 100% sure you
have ALL the code necessary to fix the comment.

Answer with strictly either YES I CAN FIX or NO I NEED MORE CONTEXT
""",
        )
        self.more_context_chain = LLMChain(
            llm=self.llm,
            prompt=self.more_context_prompt_template,
        )

        self.clarify_comment_prompt_template = PromptTemplate(
            input_variables=["raw_comment", "code_snippet"],
            template="""You are helping a tool understand a code review comment more precisely.

Here is the raw reviewer comment:
{raw_comment}

Here is the code being reviewed:
{code_snippet}

Rephrase the comment so it can be clearly understood and acted upon by an LLM.
Be specific about what to do in the code (e.g. "change this to that" or "add this here"). Rephrase the reviewer comment so that it's precise and does not overgeneralize.

Output only the rephrased, actionable version of the comment, without any explanation.
""",
        )

        self.clarify_comment_chain = LLMChain(
            llm=self.llm,
            prompt=self.clarify_comment_prompt_template,
        )

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

    def create_numbered_snippet(
        self,
        comment_start_line,
        comment_end_line,
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
        return numbered_snippet

    # def create_numbered_snippet(
    #     self,
    #     comment_start_line,
    #     comment_end_line,
    #     raw_file_content,
    #     hunk_size,
    # ):
    #     lines = raw_file_content.splitlines()
    #     total_lines = len(lines)

    #     start_line = max(comment_start_line - hunk_size, 1)
    #     end_line = min(comment_end_line + hunk_size, total_lines)

    #     snippet_lines = []
    #     for i in range(start_line, end_line + 1):
    #         line_content = lines[i - 1]
    #         # Add markers for the commented section
    #         if i == comment_start_line:
    #             snippet_lines.append(">>> START COMMENT <<<")
    #         snippet_lines.append(line_content)
    #         if i == comment_end_line:
    #             snippet_lines.append(">>> END COMMENT <<<")

    #     return "\n".join(snippet_lines)

    def ask_llm_if_needs_more_context(
        self,
        comment_content,
        snippet_preview,
    ):
        answer = self.more_context_chain.run(
            {"comment_content": comment_content, "snippet_preview": snippet_preview}
        )
        return answer

    def clarify_comment(self, raw_comment, snippet_preview):
        return self.clarify_comment_chain.run(
            {
                "raw_comment": raw_comment,
                "code_snippet": snippet_preview,
            }
        )

    def generate_fix(
        self,
        revision_id,
        diff_id,
        comment_id,
    ):
        self.hunk_size = self.default_hunk_size
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
        max_hunk_size = 30

        initial_snippet = "\n".join(
            raw_file_content.splitlines()[
                max(0, comment_start_line - 5) : comment_end_line + 5
            ]
        )
        actionability = (
            self.actionability_chain.run(
                {
                    "comment": comment_content,
                    "code": initial_snippet,
                }
            )
            .strip()
            .upper()
        )

        if actionability != "YES":
            logger.info("Comment is not actionable. Skipping.")
            return "Not Actionable"

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

            if answer == "yes i can fix":
                break
            elif answer == "no i need more context":
                self.hunk_size += step_size
            else:
                break

        clarified_comment = self.clarify_comment(
            raw_comment=comment_content, snippet_preview=snippet_preview
        )

        numbered_snippet = self.create_numbered_snippet(
            comment_start_line=comment_start_line,
            comment_end_line=comment_end_line,
            raw_file_content=raw_file_content,
            hunk_size=self.hunk_size,
        )

        generated_fix = self.generate_fix_chain.run(
            {
                "comment_start_line": comment_start_line,
                "comment_end_line": comment_end_line,
                "filepath": filepath,
                "comment_content": clarified_comment,
                "numbered_snippet": numbered_snippet,
            }
        )
        return generated_fix

    def generate_fixes_for_all_comments(self, revision_id):
        set_api_key(PHABRICATOR_API_URL, PHABRICATOR_API_TOKEN)

        revisions = get(rev_ids=[int(revision_id)])
        if not revisions:
            raise Exception(f"No revision found for ID {revision_id}")

        revision = revisions[0]
        latest_diff_id = int(revision["fields"]["diffID"])
        comment_map = {}

        reviewer_phids = {
            reviewer["reviewerPHID"]
            for reviewer in revision.get("attachments", {})
            .get("reviewers", {})
            .get("reviewers", [])
        }

        for transaction in revision.get("transactions", []):
            if transaction["type"] != "inline":
                continue

            author_phid = transaction["authorPHID"]
            if author_phid not in reviewer_phids:
                continue

            for comment in transaction.get("comments", []):
                comment_id = comment["id"]
                try:
                    fix = self.generate_fix(
                        revision_id=revision_id,
                        diff_id=latest_diff_id,
                        comment_id=comment_id,
                    )
                    comment_map[comment_id] = fix
                except Exception as e:
                    logger.warning(
                        f"Error generating fix for comment {comment_id}: {e}"
                    )
                    comment_map[comment_id] = f"Error: {e}"

        return comment_map
