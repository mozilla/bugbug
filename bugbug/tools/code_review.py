# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import enum
import json
import os
import re
from abc import ABC, abstractmethod
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import cached_property
from itertools import chain
from logging import INFO, basicConfig, getLogger
from typing import Iterable, Literal, Optional

import tenacity
from langchain.chains import LLMChain
from langchain.prompts import PromptTemplate
from langchain_core.language_models import BaseLanguageModel
from langchain_openai import OpenAIEmbeddings
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent
from libmozdata.phabricator import ConduitError
from tqdm import tqdm
from unidiff import Hunk, PatchedFile, PatchSet
from unidiff.errors import UnidiffParseError

from bugbug import bugzilla, db, phabricator, utils
from bugbug.code_search.function_search import FunctionSearch
from bugbug.generative_model_tool import (
    GenerativeModelTool,
    get_tokenizer,
)
from bugbug.tools.agent_tools import (
    CodeReviewContext,
    create_find_function_definition_tool,
    expand_context,
)
from bugbug.utils import get_secret
from bugbug.vectordb import PayloadScore, QueryFilter, VectorDB, VectorPoint

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
    hunk_start_line: int | None = None
    hunk_end_line: int | None = None
    is_generated: bool | None = None
    explanation: str | None = None
    order: int | None = None


class ModelResultError(Exception):
    """Occurs when the model returns an unexpected result."""


class FileNotInPatchError(ModelResultError):
    """Occurs when the file in the model result is not part of the patch."""


class HunkNotInPatchError(ModelResultError):
    """Occurs when the hunk in the model result is not part of the patch."""


class LargeDiffError(Exception):
    """Occurs when the diff is too large to be processed."""


TARGET_SOFTWARE: str | None = None

PROMPT_TEMPLATE_SUMMARIZATION = """You are an expert reviewer for {experience_scope}, with experience on source code reviews.

Please, analyze the code provided and report a summarization about the new changes; for that, focus on the coded added represented by lines that start with "+".

The summarization should have two parts:
    1. **Intent**: Describe the intent of the changes, what they are trying to achieve, and how they relate to the bug or feature request.
    2. **Structure**: Describe the structure of the changes, including any new functions, classes, or modules introduced, and how they fit into the existing codebase.

Do not include any code in the summarization, only a description of the changes.

**Bug title**:
{bug_title}

**Commit message**:
{patch_title}

**Diff**:
{patch}"""

PROMPT_TEMPLATE_REVIEW = """**Task**:

Generate high-quality code review comments for the patch provided below.

**Instructions**:

1. **Analyze the Changes**:

   * Understand the intent and structure of the changes in the patch.
   * Use the provided summarization for context, but prioritize what's visible in the diff.

2. **Identify Issues**:

   * Detect bugs, logical errors, performance concerns, security issues, or violations of the `{target_code_consistency}` coding standards.
   * Focus only on **new or changed lines** (lines beginning with `+`).

3. **Assess Confidence and Order**:

   * **Sort the comments by descending confidence and importance**:
     * Start with issues you are **certain are valid**.
     * Also, prioritize important issues that you are **confident about**.
     * Follow with issues that are **plausible but uncertain** (possible false positives).
   * Assign each comment a numeric `order`, starting at 1.

4. **Write Clear, Constructive Comments**:

   * Use **direct, declarative language**.
   * Keep comments **short and specific**.
   * Focus strictly on code-related concerns.
   * Avoid hedging language (e.g., don’t use “maybe”, “might want to”, or form questions).
   * Avoid repeating what the code is doing unless it supports your critique.

5. **Use available tools**:
    * Consider using available tools to better understand the context of the code changes you are reviewing.
    * Limit the use of tools to only when you need more context to analyze the code changes.

**Avoid Comments That**:

* Refer to unmodified code (lines without a `+` prefix).
* Ask for verification or confirmation (e.g., “Check if…”).
* Provide praise or restate obvious facts.
* Focus on testing.

---

**Output Format**:

Respond only with a **JSON list**. Each object must contain the following fields:

* `"file"`: The relative path to the file the comment applies to.
* `"code_line"`: The number of the specific changed line of code that the comment refers to.
* `"comment"`: A concise review comment.
* `"explanation"`: A brief rationale for the comment, including how confident you are and why.
* `"order"`: An integer indicating the comment’s priority (1 = highest confidence/importance).

---

**Examples**:

{comment_examples}
{approved_examples}

---

**Patch Summary**:

{patch_summarization}

---

**Patch to Review**:

{patch}
"""


TEMPLATE_COMMENT_EXAMPLE = """Patch example {example_number}:

{patch}

Review comments for example {example_number}:

{comments}"""


PROMPT_TEMPLATE_FILTERING_ANALYSIS = """Filter review comments to keep those that:
- are consistent with the {target_code_consistency} source code;
- focus on reporting possible bugs, functional regressions, issues, or similar concerns;
- report readability or design concerns.

Exclude comments that:
- only describe the change;
- restate obvious facts like renamed variables or replaced code;
- include praising;
- ask if changes are intentional or ask to ensure things exist.

Only return a valid JSON list. Do not drop any key from the JSON objects.

Comments:
{comments}

As examples of not expected comments, not related to the current patch, please, check some below:
    - {rejected_examples}
"""


DEFAULT_REJECTED_EXAMPLES = """Please note that these are minor improvements and the overall quality of the patch is good. The documentation is being expanded in a clear and structured way, which will likely be beneficial for future development.
    - Please note that these are just suggestions and the code might work perfectly fine as it is. It's always a good idea to test all changes thoroughly to ensure they work as expected.
    - Overall, the patch seems to be well implemented with no major concerns. The developers have made a conscious decision to align with Chrome's behavior, and the reasoning is well documented.
    - There are no complex code changes in this patch, so there's no potential for major readability regressions or bugs introduced by the changes.
    - The `focus(...)` method is called without checking if the element and its associated parameters exist or not. It would be better to check if the element exists before calling the `focus()` method to avoid potential errors.
    - It's not clear if the `SearchService.sys.mjs` file exists or not. If it doesn't exist, this could cause an error. Please ensure that the file path is correct.
    - This is a good addition to the code."""


STATIC_COMMENT_EXAMPLES = [
    {
        "comment": {
            "filename": "netwerk/streamconv/converters/mozTXTToHTMLConv.cpp",
            "start_line": 1211,
            "content": "You are using `nsAutoStringN<256>` instead of `nsString`. This is a good change as `nsAutoStringN<256>` is more efficient for small strings. However, you should ensure that the size of `tempString` does not exceed 256 characters, as `nsAutoStringN<256>` has a fixed size.",
            "explanation": "THE JUSTIFICATION GOES HERE",
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
            "explanation": "THE JUSTIFICATION GOES HERE",
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
            "explanation": "THE JUSTIFICATION GOES HERE",
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
    def base_commit_hash(self) -> str: ...

    @property
    @abstractmethod
    def raw_diff(self) -> str: ...

    @property
    @abstractmethod
    def date_created(self) -> datetime: ...

    @cached_property
    def patch_set(self) -> PatchSet:
        return PatchSet.from_string(self.raw_diff)

    @property
    @abstractmethod
    def bug_title(self) -> str:
        """Return the title of the bug associated with this patch."""
        ...

    @property
    @abstractmethod
    def patch_title(self) -> str:
        """Return the title of the patch."""
        ...

    @abstractmethod
    def get_old_file(self, file_path: str) -> str:
        """Return the contents of a file before the patch was applied."""
        ...


class PhabricatorPatch(Patch):
    def __init__(self, patch_id: str) -> None:
        super().__init__(patch_id)

        self.diff_id = int(patch_id)

    def _get_file(self, file_path: str, is_before_patch: bool) -> str:
        for changeset in self._changesets:
            if changeset["fields"]["path"]["displayPath"] == file_path:
                break
        else:
            raise FileNotFoundError(f"File {file_path} not found in changesets")

        changeset_id = changeset["id"]

        view = "old" if is_before_patch else "new"
        r = utils.get_session("phabricator_web").get(
            f"https://phabricator.services.mozilla.com/differential/changeset/?view={view}&ref={changeset_id}",
            headers={
                "User-Agent": utils.get_user_agent(),
            },
        )
        r.raise_for_status()

        return r.text

    def get_old_file(self, file_path: str) -> str:
        return self._get_file(file_path, is_before_patch=True)

    @cached_property
    def _changesets(self) -> list[dict]:
        assert phabricator.PHABRICATOR_API is not None

        diff = self._diff_metadata

        changesets = phabricator.PHABRICATOR_API.request(
            "differential.changeset.search",
            constraints={"diffPHIDs": [diff["phid"]]},
        )["data"]

        return changesets

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
    def _diff_metadata(self) -> dict:
        assert phabricator.PHABRICATOR_API is not None
        diffs = phabricator.PHABRICATOR_API.search_diffs(diff_id=self.diff_id)
        assert len(diffs) == 1
        diff = diffs[0]

        return diff

    @cached_property
    def base_commit_hash(self) -> str:
        diff = self._diff_metadata

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

    @property
    def date_created(self) -> datetime:
        return datetime.fromtimestamp(self._diff_metadata["dateCreated"])

    @cached_property
    def _revision_metadata(self) -> dict:
        assert phabricator.PHABRICATOR_API is not None

        revision = phabricator.PHABRICATOR_API.load_revision(
            rev_phid=self._diff_metadata["revisionPHID"]
        )

        return revision

    @cached_property
    def _bug_metadata(self) -> dict | None:
        id = self.bug_id
        bugs = bugzilla.get(id)

        if id not in bugs:
            logger.warning(
                "Bug %d not found in Bugzilla. This might be a private bug.", id
            )
            return None

        return bugs[id]

    @property
    def bug_id(self) -> int:
        return int(self._revision_metadata["fields"]["bugzilla.bug-id"])

    @cached_property
    def bug_title(self) -> str:
        if not self._bug_metadata:
            # Use a placeholder when the bug metadata is not available
            return "--"

        return self._bug_metadata["summary"]

    @cached_property
    def patch_title(self) -> str:
        return self._revision_metadata["fields"]["title"]


def create_bug_timeline(comments: list[dict], history: list[dict]) -> list[str]:
    """Create a unified timeline from bug history and comments."""
    events = []

    ignored_fields = {"cc", "flagtypes.name"}

    # Add history events
    for event in history:
        changes = [
            change
            for change in event["changes"]
            if change["field_name"] not in ignored_fields
        ]
        if not changes:
            continue

        events.append(
            {
                "time": event["when"],
                "type": "change",
                "who": event["who"],
                "details": changes,
            }
        )

    # Add comments
    for comment in comments:
        events.append(
            {
                "time": comment["time"],
                "type": "comment",
                "who": comment["author"],
                "id": comment["id"],
                "count": comment["count"],
                "text": comment["text"],
            }
        )

    # Sort by timestamp
    events.sort(key=lambda x: (x["time"], x["type"] == "change"))

    # Format timeline
    timeline = []

    last_event = None
    for event in events:
        date = event["time"][:10]
        time = event["time"][11:19]

        if last_event and last_event["time"] != event["time"]:
            timeline.append("---\n")

        last_event = event

        if event["type"] == "comment":
            timeline.append(
                f"**{date} {time}** - Comment #{event['count']} by {event['who']}"
            )
            timeline.append(f"{event['text']}\n")
        else:
            timeline.append(f"**{date} {time}** - Changes by {event['who']}")
            for change in event["details"]:
                field = change.get("field_name", "unknown")
                old = change.get("removed", "")
                new = change.get("added", "")
                if old or new:
                    timeline.append(f"  - {field}: '{old}' → '{new}'")
            timeline.append("")

    return timeline


def bug_dict_to_markdown(bug):
    md_lines = []

    # Header with bug ID and summary
    md_lines.append(
        f"# Bug {bug.get('id', 'Unknown')} - {bug.get('summary', 'No summary')}"
    )
    md_lines.append("")

    # Basic Information
    md_lines.append("## Basic Information")
    md_lines.append(f"- **Status**: {bug.get('status', 'Unknown')}")
    md_lines.append(f"- **Severity**: {bug.get('severity', 'Unknown')}")
    md_lines.append(f"- **Priority**: {bug.get('priority', 'Unknown')}")
    md_lines.append(f"- **Product**: {bug.get('product', 'Unknown')}")
    md_lines.append(f"- **Component**: {bug.get('component', 'Unknown')}")
    md_lines.append(f"- **Version**: {bug.get('version', 'Unknown')}")
    md_lines.append(f"- **Platform**: {bug.get('platform', 'Unknown')}")
    md_lines.append(f"- **OS**: {bug.get('op_sys', 'Unknown')}")
    md_lines.append(f"- **Created**: {bug.get('creation_time', 'Unknown')}")
    md_lines.append(f"- **Last Updated**: {bug.get('last_change_time', 'Unknown')}")

    if bug.get("url"):
        md_lines.append(f"- **Related URL**: {bug['url']}")

    if bug.get("keywords"):
        md_lines.append(f"- **Keywords**: {', '.join(bug['keywords'])}")

    md_lines.append("")

    # People Involved
    md_lines.append("## People Involved")

    creator_detail = bug.get("creator_detail", {})
    if creator_detail:
        creator_name = creator_detail.get(
            "real_name",
            creator_detail.get("nick", creator_detail.get("email", "Unknown")),
        )
        md_lines.append(
            f"- **Reporter**: {creator_name} ({creator_detail.get('email', 'No email')})"
        )

    assignee_detail = bug.get("assigned_to_detail", {})
    if assignee_detail:
        assignee_name = assignee_detail.get(
            "real_name",
            assignee_detail.get("nick", assignee_detail.get("email", "Unknown")),
        )
        md_lines.append(
            f"- **Assignee**: {assignee_name} ({assignee_detail.get('email', 'No email')})"
        )

    # CC List (summarized)
    cc_count = len(bug.get("cc", []))
    if cc_count > 0:
        md_lines.append(f"- **CC Count**: {cc_count} people")

    md_lines.append("")

    # Dependencies and Relationships
    relationships = []
    if bug.get("blocks"):
        relationships.append(f"**Blocks**: {', '.join(map(str, bug['blocks']))}")
    if bug.get("depends_on"):
        relationships.append(
            f"**Depends on**: {', '.join(map(str, bug['depends_on']))}"
        )
    if bug.get("regressed_by"):
        relationships.append(
            f"**Regressed by**: {', '.join(map(str, bug['regressed_by']))}"
        )
    if bug.get("duplicates"):
        relationships.append(
            f"**Duplicates**: {', '.join(map(str, bug['duplicates']))}"
        )
    if bug.get("see_also"):
        relationships.append(f"**See also**: {', '.join(bug['see_also'])}")

    if relationships:
        md_lines.append("## Bug Relationships")
        for rel in relationships:
            md_lines.append(f"- {rel}")
        md_lines.append("")

    timeline = create_bug_timeline(bug["comments"], bug["history"])
    if timeline:
        md_lines.append("## Bug Timeline")
        md_lines.append("")
        md_lines.extend(timeline)

    return "\n".join(md_lines)


class Bug:
    """Represents a Bugzilla bug from bugzilla.mozilla.org."""

    def __init__(self, data: dict):
        self.metadata = data

    @staticmethod
    def get(bug_id: int) -> "Bug":
        from libmozdata.bugzilla import Bugzilla

        bugs: list[dict] = []
        Bugzilla(
            bug_id,
            include_fields=["_default", "comments", "history"],
            bughandler=lambda bug, data: data.append(bug),
            bugdata=bugs,
        ).get_data().wait()

        if len(bugs) == 0:
            raise ValueError(f"Bug {bug_id} not found")

        bug_data = bugs[0]
        assert bug_data["id"] == bug_id

        return Bug(bug_data)

    def to_md(self) -> str:
        """Return a markdown representation of the bug."""
        return bug_dict_to_markdown(self.metadata)


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
                logger.warning(
                    "Multiple matching hunks found for comment %s in file %s",
                    comment.id,
                    comment.filename,
                )
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
            # We want to keep all generated comments
            if comment.is_generated:
                return True

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
            except ConduitError:
                logger.warning("Failed to load %d", diff_id)
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

                if len(transaction["comments"]) != 1:
                    # Follow up: https://github.com/mozilla/bugbug/issues/4218
                    logger.warning(
                        "Unexpected number of comments in transaction %s",
                        transaction["id"],
                    )
                    continue

                transaction_comment = transaction["comments"][0]
                comment_content = transaction_comment["content"]["raw"]
                is_generated = (
                    # This includes comments generated by Review Helper, but
                    # excludes any comments that have been edited by the user.
                    "> This comment was generated automatically and has been approved by"
                    in comment_content
                )

                # Ignore bot comments, except the ones by Review Helper
                if (
                    transaction["authorPHID"] == "PHID-USER-cje4weq32o3xyuegalpj"
                    and not is_generated
                ):
                    continue

                comment_id = transaction_comment["id"]
                date_created = transaction_comment["dateCreated"]
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
                    filename=filename,
                    start_line=start_line,
                    end_line=end_line,
                    content=comment_content,
                    on_removed_code=on_removed_code,
                    id=comment_id,
                    date_created=date_created,
                    date_modified=date_modified,
                    is_done=is_done,
                    is_generated=is_generated,
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
    def _revision_metadata(self) -> dict:
        import swarm

        revisions = swarm.get(self.auth, rev_ids=[self.rev_id])
        assert len(revisions) == 1

        return revisions[0]

    @property
    def raw_diff(self) -> str:
        revision = self._revision_metadata

        return revision["fields"]["diff"]

    @cached_property
    def base_commit_hash(self) -> str:
        raise NotImplementedError

    @property
    def date_created(self) -> datetime:
        revision = self._revision_metadata

        return datetime.fromtimestamp(revision["fields"]["created"])

    @cached_property
    def patch_title(self) -> str:
        raise NotImplementedError

    @cached_property
    def bug_title(self) -> str:
        raise NotImplementedError

    def get_old_file(self, file_path: str) -> str:
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
    hunks_based_on_added = (
        hunk
        for hunk in file
        if hunk.target_start <= line_number <= hunk.target_start + hunk.target_length
    )
    hunks_based_on_deleted = (
        hunk
        for hunk in file
        if hunk.source_start <= line_number <= hunk.source_start + hunk.source_length
    )

    try:
        hunk = next(chain(hunks_based_on_added, hunks_based_on_deleted))
    except StopIteration as e:
        raise HunkNotInPatchError("Line number not found in the patch") from e

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


def parse_model_output(output: str) -> list[dict]:
    # TODO: This function should raise an exception when the output is not valid
    # JSON. The caller can then decide how to handle the error.
    # For now, we just log the error and return an empty list.

    json_opening_tag = output.find("```json")
    if json_opening_tag != -1:
        json_closing_tag = output.rfind("```")
        if json_closing_tag == -1:
            logger.error("No closing ``` found for JSON in model output: %s", output)
            return []
        output = output[json_opening_tag + len("```json") : json_closing_tag]

    try:
        comments = json.loads(output)
    except json.JSONDecodeError:
        logger.error("Failed to parse JSON from model output: %s", output)
        return []

    return comments


def generate_processed_output(output: str, patch: PatchSet) -> Iterable[InlineComment]:
    comments = parse_model_output(output)

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
            start_line=line_number,
            end_line=line_number,
            hunk_start_line=scope["line_start"],
            hunk_end_line=scope["line_end"],
            content=comment["comment"],
            on_removed_code=not scope["has_added_lines"],
            explanation=comment["explanation"],
            order=comment["order"],
        )


class CodeReviewTool(GenerativeModelTool):
    version = "0.0.1"

    def __init__(
        self,
        llm: BaseLanguageModel,
        function_search: Optional[FunctionSearch] = None,
        review_comments_db: Optional["ReviewCommentsDB"] = None,
        show_patch_example: bool = False,
        verbose: bool = True,
        suggestions_feedback_db: Optional["SuggestionsFeedbackDB"] = None,
        target_software: Optional[str] = None,
    ) -> None:
        super().__init__()

        self.target_software = target_software or TARGET_SOFTWARE

        self._tokenizer = get_tokenizer(
            llm.model_name if hasattr(llm, "model_name") else ""
        )
        self.is_experiment_env = os.getenv("EXPERIMENT_ENV", "no").lower() in (
            "1",
            "yes",
            "true",
        )
        if self.is_experiment_env:
            print(
                "---------------------- WARNING ---------------------\n"
                "You are using the experiment environment.\n"
                "This environment is not intended for production use.\n"
                "----------------------------------------------------"
            )

        experience_scope = (
            f"the {self.target_software} source code"
            if self.target_software
            else "a software project"
        )

        self.summarization_chain = LLMChain(
            prompt=PromptTemplate.from_template(
                PROMPT_TEMPLATE_SUMMARIZATION,
                partial_variables={"experience_scope": experience_scope},
            ),
            llm=llm,
            verbose=verbose,
        )
        self.filtering_chain = LLMChain(
            prompt=PromptTemplate.from_template(
                PROMPT_TEMPLATE_FILTERING_ANALYSIS,
                partial_variables={
                    "target_code_consistency": self.target_software or "rest of the"
                },
            ),
            llm=llm,
            verbose=verbose,
        )

        tools = [expand_context]
        if function_search:
            tools.append(create_find_function_definition_tool(function_search))

        self.agent = create_react_agent(
            llm,
            tools,
            prompt=f"You are an expert reviewer for {experience_scope}, with experience on source code reviews.",
        )

        self.review_comments_db = review_comments_db

        self.show_patch_example = show_patch_example

        self.verbose = verbose

        self.suggestions_feedback_db = suggestions_feedback_db

    def count_tokens(self, text):
        return len(self._tokenizer.encode(text))

    def _generate_suggestions(self, patch: Patch):
        formatted_patch = format_patch_set(patch.patch_set)
        if formatted_patch == "":
            return None

        output_summarization = self.summarization_chain.invoke(
            {
                "patch": formatted_patch,
                "bug_title": patch.bug_title,
                "patch_title": patch.patch_title,
            },
            return_only_outputs=True,
        )["text"]

        if self.verbose:
            GenerativeModelTool._print_answer(output_summarization)

        created_before = patch.date_created if self.is_experiment_env else None
        message = PROMPT_TEMPLATE_REVIEW.format(
            patch=patch.raw_diff,
            patch_summarization=output_summarization,
            comment_examples=self._get_comment_examples(patch, created_before),
            approved_examples=self._get_generated_examples(patch, created_before),
            target_code_consistency=self.target_software or "rest of the",
        )

        try:
            for chunk in self.agent.stream(
                {"messages": [{"role": "user", "content": message}]},
                context=CodeReviewContext(patch=patch),
                stream_mode="values",
            ):
                result = chunk
        except GraphRecursionError as e:
            raise ModelResultError("The model could not complete the review") from e

        return result["messages"][-1].content

    def run(self, patch: Patch) -> list[InlineComment] | None:
        if self.count_tokens(patch.raw_diff) > 21000:
            raise LargeDiffError("The diff is too large")

        output = self._generate_suggestions(patch)

        unfiltered_suggestions = parse_model_output(output)
        if not unfiltered_suggestions:
            logger.info("No suggestions were generated")
            return []

        rejected_examples = (
            "\n    - ".join(self.get_similar_rejected_comments(unfiltered_suggestions))
            if self.suggestions_feedback_db
            else DEFAULT_REJECTED_EXAMPLES
        )

        raw_output = self.filtering_chain.invoke(
            {
                "comments": output,
                "rejected_examples": rejected_examples,
            },
            return_only_outputs=True,
        )["text"]

        if self.verbose:
            GenerativeModelTool._print_answer(raw_output)

        return list(generate_processed_output(raw_output, patch.patch_set))

    def _get_generated_examples(self, patch, created_before: datetime | None = None):
        """Get examples of comments that were generated by an LLM.

        Since the comments are posted, it means that they were approved by a
        reviewer. Thus, we can use them as examples of good comments.
        """
        if not self.review_comments_db:
            return ""

        comment_examples = [
            result.payload["comment"]["content"]
            for result in self.review_comments_db.find_similar_patch_comments(
                patch, limit=5, generated=True, created_before=created_before
            )
        ]
        if not comment_examples:
            return ""

        template = """
**Examples of comments that you suggested on other patches and developers found useful**:

- {comment_examples}
"""

        return template.format(comment_examples="\n    - ".join(comment_examples))

    def _get_comment_examples(self, patch, created_before: datetime | None = None):
        comment_examples = []

        if self.review_comments_db:
            comment_examples = [
                result.payload
                for result in self.review_comments_db.find_similar_patch_comments(
                    patch, limit=10, generated=False, created_before=created_before
                )
            ]

        if not comment_examples:
            comment_examples = STATIC_COMMENT_EXAMPLES
        else:
            for example in comment_examples:
                example["comment"]["explanation"] = "THE JUSTIFICATION GOES HERE"

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

    def get_similar_rejected_comments(self, suggestions) -> Iterable[str]:
        if not self.suggestions_feedback_db:
            raise Exception("Suggestions feedback database is not available")

        num_examples_per_suggestion = 10 // len(suggestions) or 1
        seen_ids: set[int] = set()

        for suggestion in suggestions:
            similar_rejected_suggestions = (
                self.suggestions_feedback_db.find_similar_rejected_suggestions(
                    suggestion["comment"],
                    limit=num_examples_per_suggestion,
                    excluded_ids=seen_ids,
                )
            )
            for rejected_suggestion in similar_rejected_suggestions:
                seen_ids.add(rejected_suggestion.id)
                yield rejected_suggestion.comment


class ReviewCommentsDB:
    NAV_PATTERN = re.compile(r"\{nav, [^}]+\}")
    WHITESPACE_PATTERN = re.compile(r"[\n\s]+")

    def __init__(self, vector_db: VectorDB) -> None:
        self.vector_db = vector_db
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-large", api_key=get_secret("OPENAI_API_KEY")
        )

    def clean_comment(self, comment: str):
        # We do not want to keep the LLM note in the comment, it is not useful
        # when using the comment as examples.
        llm_note_index = comment.find("> This comment was generated automatically ")
        if llm_note_index != -1:
            comment = comment[:llm_note_index]

        # TODO: use the nav info instead of removing it
        comment = self.NAV_PATTERN.sub("", comment)
        comment = self.WHITESPACE_PATTERN.sub(" ", comment)
        comment = comment.strip()

        return comment

    def add_comments_by_hunk(self, items: Iterable[tuple[Hunk, InlineComment]]):
        point_ids = set(self.vector_db.get_existing_ids())
        logger.info("Will skip %d comments that already exist", len(point_ids))

        def vector_points():
            for hunk, comment in items:
                if comment.id in point_ids:
                    continue

                str_hunk = str(hunk)
                vector = self.embeddings.embed_query(str_hunk)

                comment_data = asdict(comment)
                comment_data["content"] = self.clean_comment(comment.content)
                payload = {
                    "hunk": str_hunk,
                    "comment": comment_data,
                    "version": 2,
                }

                yield VectorPoint(id=comment.id, vector=vector, payload=payload)

        self.vector_db.insert(vector_points())

    def find_similar_hunk_comments(
        self,
        hunk: Hunk,
        generated: bool | None = None,
        created_before: datetime | None = None,
    ):
        return self.vector_db.search(
            self.embeddings.embed_query(str(hunk)),
            filter=(
                QueryFilter(
                    must_match=(
                        {"comment.is_generated": generated}
                        if generated is not None
                        else None
                    ),
                    must_range=(
                        {
                            "comment.date_created": {
                                "lt": created_before.timestamp(),
                            }
                        }
                        if created_before is not None
                        else None
                    ),
                )
            ),
        )

    def find_similar_patch_comments(
        self,
        patch: Patch,
        limit: int,
        generated: bool | None = None,
        created_before: datetime | None = None,
    ):
        assert limit > 0, "Limit must be greater than 0"

        patch_set = PatchSet.from_string(patch.raw_diff)

        # We want to avoid returning the same comment multiple times. Thus, if
        # a comment matches multiple hunks, we will only consider it once.
        max_score_per_comment: dict = {}
        for patched_file in patch_set:
            if not patched_file.is_modified_file:
                continue

            for hunk in patched_file:
                for result in self.find_similar_hunk_comments(
                    hunk, generated, created_before
                ):
                    if result is not None and (
                        result.id not in max_score_per_comment
                        or result.score > max_score_per_comment[result.id].score
                    ):
                        max_score_per_comment[result.id] = result

        return sorted(max_score_per_comment.values())[-limit:]


class EvaluationAction(enum.Enum):
    APPROVE = 1
    REJECT = 2
    IGNORE = 3


@dataclass
class SuggestionFeedback:
    id: int
    comment: str
    file_path: str
    action: Literal["APPROVE", "REJECT", "IGNORE"]
    user: str

    @staticmethod
    def from_payload_score(point: PayloadScore):
        return SuggestionFeedback(
            id=point.id,
            comment=point.payload["comment"],
            file_path=point.payload["file_path"],
            action=point.payload["action"],
            user=point.payload["user"],
        )


class SuggestionsFeedbackDB:
    def __init__(self, vector_db: VectorDB) -> None:
        self.vector_db = vector_db
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-large", api_key=get_secret("OPENAI_API_KEY")
        )

    def add_suggestions_feedback(self, suggestions: Iterable[SuggestionFeedback]):
        def vector_points():
            for suggestion in suggestions:
                vector = self.embeddings.embed_query(suggestion.comment)
                payload = {
                    "comment": suggestion.comment,
                    "file_path": suggestion.file_path,
                    "action": suggestion.action,
                    "user": suggestion.user,
                }

                yield VectorPoint(id=suggestion.id, vector=vector, payload=payload)

        self.vector_db.insert(vector_points())

    def find_similar_suggestions(self, comment: str):
        return (
            SuggestionFeedback.from_payload_score(point)
            for point in self.vector_db.search(self.embeddings.embed_query(comment))
        )

    def find_similar_rejected_suggestions(
        self, comment: str, limit: int, excluded_ids: Iterable[int] = ()
    ):
        return (
            SuggestionFeedback.from_payload_score(point)
            for point in self.vector_db.search(
                self.embeddings.embed_query(comment),
                filter=QueryFilter(
                    must_match={"action": "REJECT"},
                    must_not_has_id=list(excluded_ids),
                ),
                limit=limit,
            )
        )
