# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import asdict, dataclass
from logging import INFO, basicConfig, getLogger
from typing import Iterable, Optional

import tenacity
from langchain.chains import ConversationChain, LLMChain
from langchain.memory import ConversationBufferMemory
from langchain.prompts import PromptTemplate
from langchain_openai import OpenAIEmbeddings
from tenacity import retry, retry_if_exception_type, stop_after_attempt
from tqdm import tqdm
from unidiff import Hunk, PatchedFile, PatchSet
from unidiff.errors import UnidiffParseError

from bugbug import db, phabricator, swarm
from bugbug.generative_model_tool import GenerativeModelTool
from bugbug.utils import get_secret
from bugbug.vectordb import VectorDB, VectorPoint

basicConfig(level=INFO)
logger = getLogger(__name__)


@dataclass
class InlineComment:
    filename: str
    start_line: int
    end_line: int
    content: str
    on_removed_code: bool | None
    id: int | None = None
    date_created: int | None = None


class ModelResultError(Exception):
    """Occurs when the model returns an unexpected result."""


class FileNotInPatchError(ModelResultError):
    """Occurs when the file in the model result is not part of the patch."""


class HunkNotInPatchError(ModelResultError):
    """Occurs when the hunk in the model result is not part of the patch."""


PROMPT_TEMPLATE_SUMMARIZATION = """You are an expert reviewer for the Mozilla Firefox source code, with experience on source code reviews.

Please, analyze the code provided and report a summarization about the new changes; for that, focus on the coded added represented by lines that start with "+".

{patch}"""

PROMPT_TEMPLATE_REVIEW = """You will be given a task for generate a code review for the patch below. Use the following steps to solve it.

{patch}

1. Understand the changes done in the patch by reasoning about the summarization as previously reported.
2. Identify possible code snippets that might result in possible bugs, major readability regressions, and similar concerns.
3. Reason about each identified problem to make sure they are valid. Have in mind, your review must be consistent with the source code in Firefox. As valid comments, consider the examples below:
{comment_examples}
4. Filter out comments that focuses on documentation, comments, error handling, tests, and confirmation whether objects, methods and files exist or not.
5. Final answer: Write down the comments and report them using the JSON format previously adopted for the valid comment examples."""

PROMPT_TEMPLATE_FILTERING_ANALYSIS = """Please, double check the code review provided for the patch below.
Just report the comments that are:
- applicable for the patch;
- consistent with the source code in Firefox;
- focusing on reporting possible bugs, major readability regressions, or similar concerns.

Do not change the contents of the comments and the report format.
Adopt the template below:
[
    {{
        "file": "com/br/main/Pressure.java",
        "code_line": 458,
        "comment" : "In the third code block, you are using `nsAutoStringN<256>` instead of `nsString`. This is a good change as `nsAutoStringN<256>` is more efficient for small strings. However, you should ensure that the size of `tempString` does not exceed 256 characters, as `nsAutoStringN<256>` has a fixed size."
    }}
]

Review:
{review}

Patch:
{patch}

As examples of not expected comments, not related to the current patch, please, check some below:
    - Please note that these are minor improvements and the overall quality of the patch is good. The documentation is being expanded in a clear and structured way, which will likely be beneficial for future development.
    - Please note that these are just suggestions and the code might work perfectly fine as it is. It's always a good idea to test all changes thoroughly to ensure they work as expected.
    - Overall, the patch seems to be well implemented with no major concerns. The developers have made a conscious decision to align with Chrome's behavior, and the reasoning is well documented.
    - There are no complex code changes in this patch, so there's no potential for major readability regressions or bugs introduced by the changes.
    - The `focus(...)` method is called without checking if the element and its associated parameters exist or not. It would be better to check if the element exists before calling the `focus()` method to avoid potential errors.
    - It's not clear if the `SearchService.sys.mjs` file exists or not. If it doesn't exist, this could cause an error. Please ensure that the file path is correct."""


STATIC_COMMENT_EXAMPLES = [
    {
        "file": "com/br/main/Pressure.java",
        "code_line": 458,
        "comment": "In the third code block, you are using `nsAutoStringN<256>` instead of `nsString`. This is a good change as `nsAutoStringN<256>` is more efficient for small strings. However, you should ensure that the size of `tempString` does not exceed 256 characters, as `nsAutoStringN<256>` has a fixed size.",
    },
    {
        "file": "com/pt/main/atp/Texture.cpp",
        "code_line": 620,
        "comment": "The `filterAAR` function inside `#updateAllowAllRequestRules()` is created every time the method is called. Consider defining this function outside of the method to avoid unnecessary function creation.",
    },
    {
        "file": "drs/control/Statistics.cpp",
        "code_line": 25,
        "comment": "The condition in the `if` statement is a bit complex and could be simplified for better readability. Consider extracting `!Components.isSuccessCode(status) && blockList.includes(ChromeUtils.getXPCOMErrorName(status))` into a separate function with a descriptive name, such as `isBlockedError`.",
    },
]


class ReviewRequest:
    patch_id: int

    def __init__(self, patch_id) -> None:
        super().__init__()
        self.patch_id = patch_id


class Patch:
    raw_diff: str

    def __init__(self, raw_diff) -> None:
        super().__init__()
        self.raw_diff = raw_diff


class ReviewData(ABC):
    NIT_PATTERN = re.compile(r"[^a-zA-Z0-9]nit[\s:,]", re.IGNORECASE)

    @abstractmethod
    def get_review_request_by_id(self, review_id: int) -> ReviewRequest:
        raise NotImplementedError

    @abstractmethod
    def get_patch_by_id(self, patch_id: int) -> Patch:
        raise NotImplementedError

    @abstractmethod
    def get_all_inline_comments(
        self, comment_filter
    ) -> Iterable[tuple[int, list[InlineComment]]]:
        raise NotImplementedError

    def load_patch_by_id(self, diff_id) -> Patch:
        """Load a patch from local cache if it exists.

        If the patch is not in the local cache it will be requested from the
        provider and cache it locally.

        Args:
            diff_id: The ID of the patch.

        Returns:
            The patch.
        """
        try:
            with open(f"patches/{diff_id}.patch", "r") as f:
                patch = Patch(f.read())
        except FileNotFoundError:
            with open(f"patches/{diff_id}.patch", "w") as f:
                patch = self.get_patch_by_id(diff_id)
                f.write(patch.raw_diff)

        return patch

    def get_matching_hunk(
        self, patched_file: PatchedFile, comment: InlineComment
    ) -> Hunk:
        def source_end(hunk: Hunk) -> int:
            return hunk.source_start + hunk.source_length

        def target_end(hunk: Hunk) -> int:
            return hunk.target_start + hunk.target_length

        if comment.on_removed_code is None:
            matching_hunks = [
                hunk
                for hunk in patched_file
                if hunk.target_start <= comment.start_line < target_end(hunk)
                or hunk.source_start <= comment.start_line < source_end(hunk)
            ]

            # If there is more than one matching hunk, choose the one where the
            # line number of the comment corresponds to an added or deleted line. We
            # prioritize added lines over deleted lines because comments are more
            # likely to be on added lines than deleted lines.
            if len(matching_hunks) > 1:
                for hunk in matching_hunks:
                    for line in hunk:
                        if line.is_added and line.target_line_no == comment.start_line:
                            return hunk

                    for line in hunk:
                        if (
                            line.is_removed
                            and line.source_line_no == comment.start_line
                        ):
                            return hunk

            if len(matching_hunks) != 0:
                logger.warning(
                    "Multiple matching hunks found for comment %s in file %s",
                    comment.id,
                    comment.filename,
                )
                return matching_hunks[0]

        elif comment.on_removed_code:
            for hunk in patched_file:
                if hunk.source_start <= comment.start_line < source_end(hunk):
                    return hunk

        else:
            for hunk in patched_file:
                if hunk.target_start <= comment.start_line < target_end(hunk):
                    return hunk

    def retrieve_comments_with_hunks(self):
        def comment_filter(comment: InlineComment):
            comment_content = comment.content

            # Ignore very short and very long comments
            if not 50 < len(comment_content) < 500:
                return False

            # Ignore comments with URLs
            if "https://" in comment_content or "http://" in comment_content:
                return False

            #  Ignore nit comments
            if self.NIT_PATTERN.search(comment_content):
                return False

            # Ignore comments with code blocks
            if "```" in comment_content:
                return False

            comment_lower = comment_content.lower()
            if any(
                phrase in comment_lower
                for phrase in [
                    "wdyt?",
                    "what do you think?",
                    "you explain",
                    "understand",
                ]
            ):
                return False

            return True

        for diff_id, comments in self.get_all_inline_comments(comment_filter):
            try:
                patch_set = PatchSet.from_string(
                    self.load_patch_by_id(diff_id).raw_diff
                )
            except UnidiffParseError:
                # TODO: use log instead of print
                print(f"Failed to parse {diff_id}")
                continue

            file_map = {
                patched_file.path: patched_file
                for patched_file in patch_set
                if patched_file.is_modified_file
            }
            for comment in comments:
                patched_file = file_map.get(comment.filename)
                if not patched_file:
                    continue

                hunk = self.get_matching_hunk(patched_file, comment)
                if not hunk:
                    continue

                yield hunk, comment


class PhabricatorReviewData(ReviewData):
    def __init__(self):
        super().__init__()
        phabricator.set_api_key(
            get_secret("PHABRICATOR_URL"), get_secret("PHABRICATOR_TOKEN")
        )

    def get_review_request_by_id(self, revision_id: int) -> ReviewRequest:
        revisions = phabricator.get(rev_ids=[int(revision_id)])
        assert len(revisions) == 1
        return ReviewRequest(revisions[0]["fields"]["diffID"])

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(7),
        wait=tenacity.wait_exponential(multiplier=1, min=16, max=64),
        reraise=True,
    )
    def get_patch_by_id(self, patch_id: int) -> Patch:
        assert phabricator.PHABRICATOR_API is not None
        raw_diff = phabricator.PHABRICATOR_API.load_raw_diff(int(patch_id))
        return Patch(raw_diff)

    def get_all_inline_comments(
        self, comment_filter
    ) -> Iterable[tuple[int, list[InlineComment]]]:
        db.download(phabricator.REVISIONS_DB)

        revision_count = sum(1 for _ in phabricator.get_revisions())
        for revision in tqdm(phabricator.get_revisions(), total=revision_count):
            diff_comments: dict[int, list[InlineComment]] = defaultdict(list)

            for transaction in revision["transactions"]:
                if transaction["type"] != "inline":
                    continue

                # Ignore replies
                if transaction["fields"]["replyToCommentPHID"] is not None:
                    continue

                # Ignore bot comments
                if transaction["authorPHID"] == "PHID-USER-cje4weq32o3xyuegalpj":
                    continue

                if len(transaction["comments"]) != 1:
                    # Follow up: https://github.com/mozilla/bugbug/issues/4218
                    logger.warning(
                        "Unexpected number of comments in transaction %s",
                        transaction["id"],
                    )
                    continue

                transaction_comment = transaction["comments"][0]
                comment_id = transaction_comment["id"]
                date_created = transaction_comment["dateCreated"]
                comment_content = transaction_comment["content"]["raw"]

                diff_id = transaction["fields"]["diff"]["id"]
                filename = transaction["fields"]["path"]
                start_line = transaction["fields"]["line"]
                end_line = (
                    transaction["fields"]["line"] + transaction["fields"]["length"] - 1
                )
                # Unfortunately, we do not have this information for a limitation
                # in Phabricator's API.
                on_removed_code = None

                # TODO: we could create an extended dataclass for this
                # instead of adding optional fields.
                comment = InlineComment(
                    filename,
                    start_line,
                    end_line,
                    comment_content,
                    on_removed_code,
                    comment_id,
                    date_created,
                )

                if not comment_filter(comment):
                    continue

                diff_comments[diff_id].append(comment)

            for diff_id, comments in diff_comments.items():
                yield diff_id, comments


class SwarmReviewData(ReviewData):
    def __init__(self):
        self.auth = {
            "user": get_secret("SWARM_USER"),
            "password": get_secret("SWARM_PASS"),
            "port": get_secret("SWARM_PORT"),
            "instance": get_secret("SWARM_INSTANCE"),
        }

    def get_review_request_by_id(self, revision_id: int) -> ReviewRequest:
        return ReviewRequest(revision_id)

    def get_patch_by_id(self, patch_id: int) -> Patch:
        revisions = swarm.get(self.auth, rev_ids=[int(patch_id)], version_l=[0, 1])
        assert len(revisions) == 1
        return Patch(revisions[0]["fields"]["diff"])

    def get_all_inline_comments(
        self, comment_filter
    ) -> Iterable[tuple[int, list[InlineComment]]]:
        # Todo
        raise NotImplementedError


review_data_classes = {
    "phabricator": PhabricatorReviewData,
    "swarm": SwarmReviewData,
}


def find_comment_scope(file: PatchedFile, line_number: int):
    for hunk in file:
        if hunk.target_start <= line_number <= hunk.target_start + hunk.target_length:
            has_added_lines = any(line.is_added for line in hunk)
            has_deleted_lines = any(line.is_removed for line in hunk)

            if has_added_lines and has_deleted_lines:
                first_line, last_line = find_mixed_lines_range(hunk)
            elif has_added_lines:
                first_line, last_line = find_added_lines_range(hunk)
            else:
                first_line, last_line = find_removed_lines_range(hunk)

            return {
                "line_start": first_line,
                "line_end": last_line,
                "has_added_lines": has_added_lines,
            }

    raise HunkNotInPatchError("Line number not found in the patch")


def find_added_lines_range(hunk: Hunk):
    added_lines = [line.target_line_no for line in hunk if line.is_added]
    return added_lines[0], added_lines[-1]


def find_removed_lines_range(hunk: Hunk):
    removed_lines = [line.source_line_no for line in hunk if line.is_removed]
    return removed_lines[0], removed_lines[-1]


def find_mixed_lines_range(hunk: Hunk):
    def get_first_line(_hunk: Hunk, default: int | None = None):
        for i, line in enumerate(_hunk):
            if line.is_context:
                continue
            if line.target_line_no is None:
                if i == 0:
                    # If this is the first line of the hunk, it
                    # means that we are adding lines is the first
                    # line in the file.
                    return default
                return _hunk[i - 1].target_line_no
            return line.target_line_no

        # This should never happen
        raise ValueError("Cannot find the line number")

    first_line = get_first_line(hunk, 1)
    last_line = get_first_line(list(reversed(hunk)))
    if last_line is None:
        _, last_line = find_added_lines_range(hunk)

    return first_line, last_line


def format_patch_set(patch_set):
    output = ""
    for patch in patch_set:
        for hunk in patch:
            output += f"Hunk Line Number: {hunk.target_start}\n"
            output += f"Filename: {patch.target_file}\n"
            output += f"Hunk: {hunk}\n"

    return output


def generate_processed_output(output: str, patch: PatchSet) -> Iterable[InlineComment]:
    output = output.strip()
    if output.startswith("```json") and output.endswith("```"):
        output = output[7:-3]

    comments = json.loads(output)

    patched_files_map = {
        patched_file.target_file: patched_file for patched_file in patch
    }

    for comment in comments:
        file_path = comment["file"]
        if not file_path.startswith("b/") and not file_path.startswith("a/"):
            file_path = "b/" + file_path

        # FIXME: currently, we do not handle renamed files

        patched_file = patched_files_map.get(file_path)
        if patched_file is None:
            raise FileNotInPatchError(
                f"The file `{file_path}` is not part of the patch: {list(patched_files_map)}"
            )

        line_number = comment["code_line"]
        if not isinstance(line_number, int):
            raise ModelResultError("Line number must be an integer")

        scope = find_comment_scope(patched_file, line_number)

        yield InlineComment(
            filename=(
                patched_file.target_file[2:]
                if scope["has_added_lines"]
                else patched_file.source_file[2:]
            ),
            start_line=scope["line_start"],
            end_line=scope["line_end"],
            content=comment["comment"],
            on_removed_code=not scope["has_added_lines"],
        )


class CodeReviewTool(GenerativeModelTool):
    version = "0.0.1"

    def __init__(
        self,
        review_comments_db: Optional["ReviewCommentsDB"],
        *args,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)

        self.summarization_chain = LLMChain(
            prompt=PromptTemplate.from_template(PROMPT_TEMPLATE_SUMMARIZATION),
            llm=self.llm,
        )
        self.filtering_chain = LLMChain(
            prompt=PromptTemplate.from_template(PROMPT_TEMPLATE_FILTERING_ANALYSIS),
            llm=self.llm,
        )

        self.review_comments_db = review_comments_db

    @retry(retry=retry_if_exception_type(ModelResultError), stop=stop_after_attempt(3))
    def run(self, patch: Patch) -> list[InlineComment] | None:
        patch_set = PatchSet.from_string(patch.raw_diff)
        formatted_patch = format_patch_set(patch_set)
        if formatted_patch == "":
            return None

        output_summarization = self.summarization_chain.invoke(
            {"patch": formatted_patch},
            return_only_outputs=True,
        )["text"]

        memory = ConversationBufferMemory()
        conversation_chain = ConversationChain(
            llm=self.llm,
            memory=memory,
        )

        memory.save_context(
            {
                "input": "You are an expert reviewer for the Mozilla Firefox source code, with experience on source code reviews."
            },
            {"output": "Sure, I'm aware of source code practices in Firefox."},
        )
        memory.save_context(
            {
                "input": 'Please, analyze the code provided and report a summarization about the new changes; for that, focus on the code added represented by lines that start with "+". '
                + patch.raw_diff
            },
            {"output": output_summarization},
        )

        comment_examples = []

        if self.review_comments_db:
            comment_examples = [
                {
                    "file": payload["comment"]["filename"],
                    "code_line": payload["comment"]["start_line"],
                    "comment": payload["comment"]["content"],
                }
                # TODO: use a smarter search to limit the number of comments and
                # diversify the examples (from different hunks, files, etc.).
                for payload in self.review_comments_db.find_similar_patch_comments(
                    patch
                )
            ]
            comment_examples = comment_examples[:10]

        if not comment_examples:
            comment_examples = STATIC_COMMENT_EXAMPLES

        output = conversation_chain.predict(
            input=PROMPT_TEMPLATE_REVIEW.format(
                patch=formatted_patch,
                comment_examples=json.dumps(comment_examples, indent=2),
            )
        )

        memory.clear()

        raw_output = self.filtering_chain.invoke(
            {"review": output, "patch": patch.raw_diff},
            return_only_outputs=True,
        )["text"]

        return list(generate_processed_output(raw_output, patch_set))


class ReviewCommentsDB:
    NAV_PATTERN = re.compile(r"\{nav, [^}]+\}")
    WHITESPACE_PATTERN = re.compile(r"[\n\s]+")

    def __init__(self, vector_db: VectorDB) -> None:
        self.vector_db = vector_db
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-large", api_key=get_secret("OPENAI_API_KEY")
        )

    def clean_comment(self, comment):
        # TODO: use the nav info instead of removing it
        comment = self.NAV_PATTERN.sub("", comment)
        comment = self.WHITESPACE_PATTERN.sub(" ", comment)
        comment = comment.strip()

        return comment

    def add_comments_by_hunk(self, items: Iterable[tuple[Hunk, InlineComment]]):
        def vector_points():
            for hunk, comment in items:
                str_hunk = str(hunk)
                vector = self.embeddings.embed_query(str_hunk)
                payload = {
                    "hunk": str_hunk,
                    "comment": asdict(comment),
                }

                yield VectorPoint(id=comment.id, vector=vector, payload=payload)

        self.vector_db.insert(vector_points())

    def find_similar_hunk_comments(self, hunk: Hunk):
        return self.vector_db.search(self.embeddings.embed_query(str(hunk)))

    def find_similar_patch_comments(self, patch: Patch):
        patch_set = PatchSet.from_string(patch.raw_diff)

        for patched_file in patch_set:
            if not patched_file.is_modified_file:
                continue

            for hunk in patched_file:
                yield from self.find_similar_hunk_comments(hunk)
