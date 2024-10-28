# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import cached_property
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

from bugbug import db, phabricator, utils
from bugbug.code_search.function_search import FunctionSearch
from bugbug.generative_model_tool import GenerativeModelTool, get_tokenizer
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
    date_modified: int | None = None
    is_done: bool | None = None


class ModelResultError(Exception):
    """Occurs when the model returns an unexpected result."""


class FileNotInPatchError(ModelResultError):
    """Occurs when the file in the model result is not part of the patch."""


class HunkNotInPatchError(ModelResultError):
    """Occurs when the hunk in the model result is not part of the patch."""


class LargeDiffError(Exception):
    """Occurs when the diff is too large to be processed."""


TARGET_SOFTWARE = None

PROMPT_TEMPLATE_SUMMARIZATION = (
    """You are an expert reviewer for"""
    + (f" the {TARGET_SOFTWARE}" if TARGET_SOFTWARE is not None else "")
    + """ source code, with experience on source code reviews.

Please, analyze the code provided and report a summarization about the new changes; for that, focus on the coded added represented by lines that start with "+".

{patch}"""
)

PROMPT_TEMPLATE_REVIEW = (
    """You will be given a task to generate a code review for the patch below. Use the following steps to solve it:
1. Understand the changes done in the patch by reasoning about the summarization as previously reported.
2. Identify possible code snippets that might result in possible bugs, major readability regressions, and similar concerns.
3. Reason about each identified problem to make sure they are valid. Have in mind, your review must be consistent with the source code"""
    + (f" in {TARGET_SOFTWARE}" if TARGET_SOFTWARE is not None else "")
    + """.
4. Filter out comments that focuses on documentation, comments, error handling, tests, and confirmation whether objects, methods and files exist or not.
5. Filter out comments that are descriptive.
6. Filter out comments that are praising (example: "This is a good addition to the code.").
7. Filter out comments that are not about added lines (have '+' symbol at the start of the line).
8. Final answer: Write down the comments and report them using the JSON format previously adopted for the valid comment examples.

As valid comments, consider the examples below:
{comment_examples}


Here is the patch that we need you to review:
{patch}"""
)


TEMPLATE_COMMENT_EXAMPLE = """Patch example {example_number}:

{patch}

Review comments for example {example_number}:

{comments}"""


PROMPT_TEMPLATE_FILTERING_ANALYSIS = (
    """Please, double check the code review provided for the patch below.
Just report the comments that are:
- applicable for the patch;
- consistent with the source code"""
    + (f" in {TARGET_SOFTWARE}" if TARGET_SOFTWARE is not None else "")
    + """;
- focusing on reporting possible bugs, major readability regressions, or similar concerns;
- filter out any descriptive comments;
- filter out any praising comments.

Do not change the contents of the comments and the report format.
Adopt the template below as the report format:
[
    {{
        "file": "com/br/main/Pressure.java",
        "code_line": 458,
        "comment" : "In the third code block, you are using `nsAutoStringN<256>` instead of `nsString`. This is a good change as `nsAutoStringN<256>` is more efficient for small strings. However, you should ensure that the size of `tempString` does not exceed 256 characters, as `nsAutoStringN<256>` has a fixed size."
    }}
]
Do not report any explanation about your choice. Only return a valid JSON list.

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
    - It's not clear if the `SearchService.sys.mjs` file exists or not. If it doesn't exist, this could cause an error. Please ensure that the file path is correct.
    - This is a good addition to the code."""
)


PROMPT_TEMPLATE_DEDUPLICATE = """Please, double check the code review comments below.
Just report the comments that are not redundant and not duplicating each other.

Do not change the contents of the comments and the report format.
Adopt the template below as the report format:
[
    {{
        "file": "com/br/main/Pressure.java",
        "code_line": 458,
        "comment" : "In the third code block, you are using `nsAutoStringN<256>` instead of `nsString`. This is a good change as `nsAutoStringN<256>` is more efficient for small strings. However, you should ensure that the size of `tempString` does not exceed 256 characters, as `nsAutoStringN<256>` has a fixed size."
    }}
]
Do not report any explanation about your choice. Only return a valid JSON list.

Review:
{review}"""


PROMPT_TEMPLATE_FURTHER_INFO = """Based on the patch provided below and its related summarization, identify the functions you need to examine for reviewing the patch.
List the names of these functions, providing only the function names, with each name on a separate line.
Avoid using list indicators such as hyphens or numbers.
If no function declaration is required, just return "".
{patch}
{summarization}"""

PROMPT_TEMPLATE_FURTHER_CONTEXT_LINES = """Based on the patch provided below and its related summarization, report the code lines more context is required.
For that, list the lines with the their associated line numbers, grouping each one on a separated line.
Avoid using list indicators such as hyphens or numbers. If no code line is required, just return "".
Examples of valid code lines:
- '152    const selector = notification.getDescription();'
- '56        file.getElement(this.targetElement());'
{patch}
{summarization}"""


STATIC_COMMENT_EXAMPLES = [
    {
        "comment": {
            "filename": "netwerk/streamconv/converters/mozTXTToHTMLConv.cpp",
            "start_line": 1211,
            "content": "You are using `nsAutoStringN<256>` instead of `nsString`. This is a good change as `nsAutoStringN<256>` is more efficient for small strings. However, you should ensure that the size of `tempString` does not exceed 256 characters, as `nsAutoStringN<256>` has a fixed size.",
        },
        "raw_hunk": """@@ -1206,11 +1206,11 @@
     } else {
       uint32_t start = uint32_t(i);
       i = aInString.FindChar('<', i);
       if (i == kNotFound) i = lengthOfInString;

-      nsString tempString;
+      nsAutoStringN<256> tempString;
       tempString.SetCapacity(uint32_t((uint32_t(i) - start) * growthRate));
       UnescapeStr(uniBuffer, start, uint32_t(i) - start, tempString);
       ScanTXT(tempString, whattodo, aOutString);
     }
   }""",
    },
    {
        "comment": {
            "filename": "toolkit/components/extensions/ExtensionDNR.sys.mjs",
            "start_line": 1837,
            "content": "The `filterAAR` function inside `#updateAllowAllRequestRules()` is created every time the method is called. Consider defining this function outside of the method to avoid unnecessary function creation.",
        },
        "raw_hunk": """@@ -1812,18 +1821,27 @@
       rulesets.push(
         this.makeRuleset(id, idx + PRECEDENCE_STATIC_RULESETS_BASE, rules)
       );
     }
     this.enabledStaticRules = rulesets;
+    this.#updateAllowAllRequestRules();
   }

   getSessionRules() {
     return this.sessionRules.rules;
   }

   getDynamicRules() {
     return this.dynamicRules.rules;
+  }
+
+  #updateAllowAllRequestRules() {
+    const filterAAR = rule => rule.action.type === "allowAllRequests";
+    this.hasRulesWithAllowAllRequests =
+      this.sessionRules.rules.some(filterAAR) ||
+      this.dynamicRules.rules.some(filterAAR) ||
+      this.enabledStaticRules.some(ruleset => ruleset.rules.some(filterAAR));
   }
 }

 function getRuleManager(extension, createIfMissing = true) {
   let ruleManager = gRuleManagers.find(rm => rm.extension === extension);""",
    },
    {
        "comment": {
            "filename": "devtools/shared/network-observer/NetworkUtils.sys.mjs",
            "start_line": 496,
            "content": "The condition in the `if` statement is a bit complex and could be simplified for better readability. Consider extracting `!Components.isSuccessCode(status) && blockList.includes(ChromeUtils.getXPCOMErrorName(status))` into a separate function with a descriptive name, such as `isBlockedError`.",
        },
        "raw_hunk": """@@ -481,26 +481,21 @@
     }
   } catch (err) {
     // "cancelledByExtension" doesn't have to be available.
   }

-  const ignoreList = [
-    // This is emitted when the request is already in the cache.
-    "NS_ERROR_PARSED_DATA_CACHED",
-    // This is emitted when there is some issues around imgages e.g When the img.src
-    // links to a non existent url. This is typically shown as a 404 request.
-    "NS_IMAGELIB_ERROR_FAILURE",
-    // This is emitted when there is a redirect. They are shown as 301 requests.
-    "NS_BINDING_REDIRECTED",
+  const blockList = [
+    // When a host is not found (NS_ERROR_UNKNOWN_HOST)
+    "NS_ERROR_UNKNOWN_HOST",
   ];

   // If the request has not failed or is not blocked by a web extension, check for
   // any errors not on the ignore list. e.g When a host is not found (NS_ERROR_UNKNOWN_HOST).
   if (
     blockedReason == 0 &&
     !Components.isSuccessCode(status) &&
-    !ignoreList.includes(ChromeUtils.getXPCOMErrorName(status))
+    blockList.includes(ChromeUtils.getXPCOMErrorName(status))
   ) {
     blockedReason = ChromeUtils.getXPCOMErrorName(status);
   }

   return { blockingExtension, blockedReason };""",
    },
]

TEMPLATE_PATCH_FROM_HUNK = """diff --git a/{filename} b/{filename}
--- a/{filename}
+++ b/{filename}
{raw_hunk}
"""


class ReviewRequest:
    patch_id: str

    def __init__(self, patch_id) -> None:
        super().__init__()
        self.patch_id = patch_id


class Patch(ABC):
    def __init__(self, patch_id: str) -> None:
        self.patch_id = patch_id

    @property
    @abstractmethod
    def base_commit_hash(self) -> str:
        ...

    @property
    @abstractmethod
    def raw_diff(self) -> str:
        ...


class PhabricatorPatch(Patch):
    def __init__(self, patch_id: str) -> None:
        super().__init__(patch_id)

        self.diff_id = int(patch_id)

    @cached_property
    def raw_diff(self) -> str:
        assert phabricator.PHABRICATOR_API is not None
        raw_diff = phabricator.PHABRICATOR_API.load_raw_diff(self.diff_id)

        return raw_diff

    @staticmethod
    def _commit_available(commit_hash: str) -> bool:
        r = utils.get_session("hgmo").get(
            f"https://hg.mozilla.org/mozilla-unified/json-rev/{commit_hash}",
            headers={
                "User-Agent": utils.get_user_agent(),
            },
        )
        return r.ok

    @cached_property
    def base_commit_hash(self) -> str:
        assert phabricator.PHABRICATOR_API is not None
        diffs = phabricator.PHABRICATOR_API.search_diffs(diff_id=self.diff_id)
        assert len(diffs) == 1
        diff = diffs[0]

        try:
            base_commit_hash = diff["refs"]["base"]["identifier"]
            if self._commit_available(base_commit_hash):
                return base_commit_hash
        except KeyError:
            pass

        end_date = datetime.fromtimestamp(diff["dateCreated"])
        start_date = datetime.fromtimestamp(diff["dateCreated"] - 86400)
        end_date_str = end_date.strftime("%Y-%m-%d %H:%M:%S")
        start_date_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
        r = utils.get_session("hgmo").get(
            f"https://hg.mozilla.org/mozilla-central/json-pushes?startdate={start_date_str}&enddate={end_date_str}&version=2&tipsonly=1",
            headers={
                "User-Agent": utils.get_user_agent(),
            },
        )
        pushes = r.json()["pushes"]
        closest_push = None
        for push_id, push in pushes.items():
            if diff["dateCreated"] - push["date"] < 0:
                continue

            if (
                closest_push is None
                or diff["dateCreated"] - push["date"]
                < diff["dateCreated"] - closest_push["date"]
            ):
                closest_push = push

        assert closest_push is not None
        return closest_push["changesets"][0]


class ReviewData(ABC):
    NIT_PATTERN = re.compile(r"[^a-zA-Z0-9]nit[\s:,]", re.IGNORECASE)

    @abstractmethod
    def get_review_request_by_id(self, review_id: int) -> ReviewRequest:
        raise NotImplementedError

    @abstractmethod
    def get_patch_by_id(self, patch_id: str) -> Patch:
        raise NotImplementedError

    @abstractmethod
    def get_all_inline_comments(
        self, comment_filter
    ) -> Iterable[tuple[int, list[InlineComment]]]:
        raise NotImplementedError

    def load_raw_diff_by_id(self, diff_id) -> str:
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
                raw_diff = f.read()
        except FileNotFoundError:
            with open(f"patches/{diff_id}.patch", "w") as f:
                patch = self.get_patch_by_id(diff_id)
                raw_diff = patch.raw_diff
                f.write(raw_diff)

        return raw_diff

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
                patch_set = PatchSet.from_string(self.load_raw_diff_by_id(diff_id))
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
        wait=tenacity.wait_exponential(multiplier=2, min=2),
        reraise=True,
    )
    def get_patch_by_id(self, patch_id: str) -> Patch:
        return PhabricatorPatch(patch_id)

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

                # store the last modified date and if the comments have been marked as done
                date_modified = transaction_comment["dateModified"]
                is_done = transaction["fields"]["isDone"]

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
                    date_modified,
                    is_done,
                )

                if not comment_filter(comment):
                    continue

                diff_comments[diff_id].append(comment)

            for diff_id, comments in diff_comments.items():
                yield diff_id, comments


class SwarmPatch(Patch):
    def __init__(self, patch_id: str, auth: dict) -> None:
        super().__init__(patch_id)
        self.auth = auth
        self.rev_id = int(patch_id)

    @cached_property
    def raw_diff(self) -> str:
        import swarm

        revisions = swarm.get(self.auth, rev_ids=[self.rev_id], version_l=[0, 1])
        assert len(revisions) == 1

        return revisions[0]["fields"]["diff"]

    @cached_property
    def base_commit_hash(self) -> str:
        raise NotImplementedError


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

    def get_patch_by_id(self, patch_id: str) -> Patch:
        return SwarmPatch(patch_id, self.auth)

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


def get_hunk_with_associated_lines(hunk):
    hunk_with_lines = ""
    for line in hunk:
        if line.is_added:
            hunk_with_lines += f"{line.target_line_no} + {line.value}"
        elif line.is_removed:
            hunk_with_lines += f"{line.source_line_no} - {line.value}"
        elif line.is_context:
            hunk_with_lines += f"{line.target_line_no}   {line.value}"

    return hunk_with_lines


def format_patch_set(patch_set):
    output = ""
    for patch in patch_set:
        for hunk in patch:
            output += f"Filename: {patch.target_file}\n"
            output += f"{get_hunk_with_associated_lines(hunk)}\n"

    return output


def get_associated_file_to_function(function_name, patch):
    for patch_by_file in patch:
        for one_patch in patch_by_file:
            if function_name in str(one_patch.source):
                return patch_by_file.path
    return None


def get_associated_file_to_line_context(context_line, patch):
    for key, value in patch.items():
        if context_line in str(value):
            return key
    return None


def parse_text_for_dict(text):
    file_content = {}
    current_filename = None
    current_lines = []

    lines = text.split("\n")
    for line in lines:
        if line.startswith("Filename:"):
            filename = line.split(":", 1)[1].strip()
            # Remove the first letter and the '/' character from the filename
            filename = filename[2:]
            current_filename = filename
            current_lines = []
        else:
            current_lines.append(line)

        # If we have content and filename, store it
        if current_filename is not None and len(current_lines) > 0:
            if file_content.get(current_filename) is not None:
                file_content[current_filename] = (
                    file_content[current_filename] + "\n" + str(line)
                )
            else:
                file_content[current_filename] = "\n".join(current_lines)

    return file_content


def len_common_path(f1, f2):
    """Find length of the common path."""
    f1_subsystems = f1.split("/")
    if f1 == f2:
        return len(f1_subsystems)

    f2_subsystems = f2.split("/")

    max_common_path_length = next(
        idx
        for idx, (sub1, sub2) in enumerate(zip(f1_subsystems, f2_subsystems))
        if sub1 != sub2
    )
    return max_common_path_length


def solve_conflict_definitions(target_path, functions):
    functions_common_path = [
        (len_common_path(target_path, fun.file), fun) for fun in functions
    ]
    max_common_path_length = max(
        [common_path_length for (common_path_length, _) in functions_common_path]
    )
    functions = [
        fun
        for (common_path_length, fun) in functions_common_path
        if common_path_length == max_common_path_length
    ]

    if len(functions) == 1:
        return functions
    else:
        return []  # could not solve conflict


def request_for_function_declarations(
    function_search, commit_hash, functions_list, patch_set
):
    functions_declarations = []

    if functions_list is not None:
        for function_name in functions_list:
            if (
                function_name != "Not found"
                and function_name != "N/A"
                and function_name != "None"
                and function_name != ""
                and len(function_name) < 50
            ):
                target_path = get_associated_file_to_line_context(
                    function_name, parse_text_for_dict(format_patch_set(patch_set))
                )

                if target_path:
                    definitions = function_search.get_function_by_name(
                        commit_hash,
                        path=target_path,
                        function_name=function_name,
                    )
                    if len(definitions) > 1:
                        definitions = solve_conflict_definitions(
                            target_path, definitions
                        )

                    collect_function_definitions(
                        functions_declarations, function_name, definitions
                    )

    return functions_declarations


def is_code_line_already_covered(code_line, target_file, function_declarations):
    for function_declaration in function_declarations:
        if (
            function_declaration[1] == target_file
            and code_line in function_declaration[2]
        ):
            return True
    return False


def collect_function_definitions(function_declarations, target_element, definitions):
    for definition in definitions:
        function_declarations.append(
            [
                target_element,
                definition.file,
                definition.source,
            ]
        )


def gather_line_context(line_context):
    r"""Reformat the line context list and remove duplicates.

    Args:
        line_context: List of lists, where each list is [line, file, function].

    Returns:
        List of tuples, where each tuple is (gathered_line, file, function). The
        'gathered_line' is a str that concatenates the 'line' with a separator
        (i.e., `\n`) that required the same function.
    """
    file_dir = {}

    for line, file, func in line_context:
        if file not in file_dir:
            file_dir[file] = {}
        if func not in file_dir[file]:
            file_dir[file][func] = []
        file_dir[file][func].append(line)

    gathered_context = []
    for file, funcs in file_dir.items():
        for func, lines in funcs.items():
            gathered_requested_lines = "\n".join(lines)
            gathered_context.append((gathered_requested_lines, file, func))
    return gathered_context


def request_for_context_lines(function_search, commit_hash, context_line_codes, patch):
    functions_declarations = []

    if context_line_codes is not None:
        for context_line in context_line_codes:
            try:
                line_number = int(re.search(r"\b(\d+)\b", context_line).group(1))
            except (AttributeError, ValueError):
                print("Unexpected Line Number Format")
                continue

            try:
                content_line = str(context_line.split(str(line_number))[1]).lstrip()[1:]
            except (IndexError, TypeError):
                print("Unexpected content line")
                continue

            target_path = get_associated_file_to_line_context(
                content_line, parse_text_for_dict(patch)
            )
            if (
                target_path
                and content_line
                and not is_code_line_already_covered(
                    content_line, target_path, functions_declarations
                )
            ):
                definitions = function_search.get_function_by_line(
                    commit_hash=commit_hash,
                    path=target_path,
                    line=line_number,
                )
                collect_function_definitions(
                    functions_declarations, context_line, definitions
                )

    functions_declarations = gather_line_context(functions_declarations)
    return functions_declarations


def get_structured_functions(target, functions_declaration):
    function_declaration_text = "\n"
    for function in functions_declaration:
        try:
            current_function_info = ""
            current_function_info += target + ": " + function[0] + "\n"
            current_function_info += "File: " + function[1] + "\n"
            if isinstance(function[2], list):
                current_function = ""
                for line in function[2]:
                    current_function += "\n" + line
                current_function_info += (
                    "Function Declaration: " + current_function + "\n\n"
                )
            else:
                current_function_info += (
                    "Function Declaration: \n" + function[2] + "\n\n"
                )
            function_declaration_text += current_function_info
        except IndexError:
            print("Function does not present all required information")
            continue

    return function_declaration_text


def generate_processed_output(output: str, patch: PatchSet) -> Iterable[InlineComment]:
    output = output.strip()
    if output.startswith("Review:"):
        output = output[len("Review:") :].strip()

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
        comment_gen_llms,
        llm=None,
        function_search: Optional[FunctionSearch] = None,
        review_comments_db: Optional["ReviewCommentsDB"] = None,
        show_patch_example: bool = False,
        verbose: bool = True,
    ) -> None:
        super().__init__()

        self.comment_gen_llms = comment_gen_llms
        self.llm = llm if llm is not None else comment_gen_llms[0]
        self._tokenizer = get_tokenizer(
            comment_gen_llms[0].model_name
            if hasattr(comment_gen_llms[0], "model_name")
            else ""
        )

        self.summarization_chain = LLMChain(
            prompt=PromptTemplate.from_template(PROMPT_TEMPLATE_SUMMARIZATION),
            llm=self.llm,
            verbose=verbose,
        )
        self.filtering_chain = LLMChain(
            prompt=PromptTemplate.from_template(PROMPT_TEMPLATE_FILTERING_ANALYSIS),
            llm=self.llm,
            verbose=verbose,
        )
        self.deduplicating_chain = LLMChain(
            prompt=PromptTemplate.from_template(PROMPT_TEMPLATE_DEDUPLICATE),
            llm=self.llm,
            verbose=verbose,
        )
        self.further_context_chain = LLMChain(
            prompt=PromptTemplate.from_template(PROMPT_TEMPLATE_FURTHER_CONTEXT_LINES),
            llm=self.llm,
            verbose=verbose,
        )
        self.further_info_chain = LLMChain(
            prompt=PromptTemplate.from_template(PROMPT_TEMPLATE_FURTHER_INFO),
            llm=self.llm,
            verbose=verbose,
        )

        self.function_search = function_search

        self.review_comments_db = review_comments_db

        self.show_patch_example = show_patch_example

        self.verbose = verbose

    def count_tokens(self, text):
        return len(self._tokenizer.encode(text))

    @retry(retry=retry_if_exception_type(ModelResultError), stop=stop_after_attempt(3))
    def run(self, patch: Patch) -> list[InlineComment] | None:
        if self.count_tokens(patch.raw_diff) > 5000:
            raise LargeDiffError("The diff is too large")

        patch_set = PatchSet.from_string(patch.raw_diff)
        formatted_patch = format_patch_set(patch_set)
        if formatted_patch == "":
            return None

        output_summarization = self.summarization_chain.invoke(
            {"patch": formatted_patch},
            return_only_outputs=True,
        )["text"]

        if self.verbose:
            GenerativeModelTool._print_answer(output_summarization)

        if self.function_search is not None:
            line_code_list = self.further_context_chain.run(
                patch=formatted_patch, summarization=output_summarization
            ).split("\n")

            if self.verbose:
                GenerativeModelTool._print_answer(line_code_list)

            requested_context_lines = request_for_context_lines(
                self.function_search,
                patch.base_commit_hash,
                line_code_list,
                formatted_patch,
            )

            function_list = self.further_info_chain.run(
                patch=patch, summarization=output_summarization
            ).split("\n")

            if self.verbose:
                GenerativeModelTool._print_answer(function_list)

            requested_functions = request_for_function_declarations(
                self.function_search,
                patch.base_commit_hash,
                function_list,
                patch_set,
            )

        output = ""
        for comment_gen_llm in self.comment_gen_llms:
            memory = ConversationBufferMemory()
            conversation_chain = ConversationChain(
                llm=comment_gen_llm,
                memory=memory,
                verbose=self.verbose,
            )

            memory.save_context(
                {
                    "input": "You are an expert reviewer for"
                    + (f" in {TARGET_SOFTWARE}" if TARGET_SOFTWARE is not None else "")
                    + " source code, with experience on source code reviews."
                },
                {
                    "output": "Sure, I'm aware of source code practices"
                    + (
                        f" in {TARGET_SOFTWARE}"
                        if TARGET_SOFTWARE is not None
                        else " in the development community"
                    )
                    + "."
                },
            )
            memory.save_context(
                {
                    "input": 'Please, analyze the code provided and report a summarization about the new changes; for that, focus on the code added represented by lines that start with "+".\n'
                    + patch.raw_diff
                },
                {"output": output_summarization},
            )

            if self.function_search is not None and len(requested_functions) > 0:
                function_declaration_text = get_structured_functions(
                    "Required Function", requested_functions
                )

                memory.save_context(
                    {
                        "input": "Attached, you can find some function definitions that are used in the current patch and might be useful to you, by giving more context about the code under analysis. "
                        + function_declaration_text
                    },
                    {
                        "output": "Okay, I will consider the provided function definitions as additional context to the given patch."
                    },
                )

            if self.function_search is not None and len(requested_context_lines) > 0:
                context_text = get_structured_functions(
                    "Requested Context for Line", requested_context_lines
                )

                memory.save_context(
                    {
                        "input": "Attached, you can also have more context of the target code under analysis."
                        + context_text
                    },
                    {
                        "output": "Okay, I will also consider the code as additional context to the given patch."
                    },
                )

            cur_output = conversation_chain.predict(
                input=PROMPT_TEMPLATE_REVIEW.format(
                    patch=formatted_patch,
                    comment_examples=self._get_comment_examples(patch),
                )
            )
            output += cur_output

            if self.verbose:
                GenerativeModelTool._print_answer(cur_output)

            memory.clear()

        if len(self.comment_gen_llms) > 1:
            output = self.deduplicating_chain.invoke(
                {"review": output},
                return_only_outputs=True,
            )["text"]

            if self.verbose:
                GenerativeModelTool._print_answer(output)

        raw_output = self.filtering_chain.invoke(
            {"review": output, "patch": patch.raw_diff},
            return_only_outputs=True,
        )["text"]

        if self.verbose:
            GenerativeModelTool._print_answer(raw_output)

        return list(generate_processed_output(raw_output, patch_set))

    def _get_comment_examples(self, patch):
        comment_examples = []

        if self.review_comments_db:
            comment_examples = [
                result.payload
                for result in self.review_comments_db.find_similar_patch_comments(
                    patch, limit=10
                )
            ]

        if not comment_examples:
            comment_examples = STATIC_COMMENT_EXAMPLES

        def format_comment(comment):
            # TODO: change the schema that we expect the model to return so we
            # can remove this function.
            return {
                "file": comment["filename"],
                "code_line": comment["start_line"],
                "comment": comment["content"],
            }

        def generate_formatted_patch_from_raw_hunk(raw_hunk, filename):
            patch = TEMPLATE_PATCH_FROM_HUNK.format(
                filename=filename, raw_hunk=raw_hunk
            )
            patch_set = PatchSet.from_string(patch)
            return format_patch_set(patch_set)

        if not self.show_patch_example:
            return json.dumps(
                [format_comment(example["comment"]) for example in comment_examples],
                indent=2,
            )

        return "\n\n".join(
            TEMPLATE_COMMENT_EXAMPLE.format(
                example_number=num + 1,
                patch=generate_formatted_patch_from_raw_hunk(
                    example["raw_hunk"], example["comment"]["filename"]
                ),
                comments=json.dumps(
                    [format_comment(example["comment"])],
                    indent=2,
                ),
            )
            for num, example in enumerate(comment_examples)
        )


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

    def find_similar_patch_comments(self, patch: Patch, limit: int):
        assert limit > 0, "Limit must be greater than 0"

        patch_set = PatchSet.from_string(patch.raw_diff)

        # We want to avoid returning the same comment multiple times. Thus, if
        # a comment matches multiple hunks, we will only consider it once.
        max_score_per_comment: dict = {}
        for patched_file in patch_set:
            if not patched_file.is_modified_file:
                continue

            for hunk in patched_file:
                for result in self.find_similar_hunk_comments(hunk):
                    if result is not None and (
                        result.id not in max_score_per_comment
                        or result.score > max_score_per_comment[result.id].score
                    ):
                        max_score_per_comment[result.id] = result

        return sorted(max_score_per_comment.values())[-limit:]
