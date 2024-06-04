# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from abc import ABC, abstractmethod
from dataclasses import dataclass

from langchain.chains import ConversationChain, LLMChain
from langchain.memory import ConversationBufferMemory
from langchain.prompts import PromptTemplate
from unidiff import Hunk, PatchedFile, PatchSet

from bugbug import phabricator, swarm
from bugbug.generative_model_tool import GenerativeModelTool
from bugbug.utils import get_secret
from bugbug.tools.rag.rag_qdrant import RAGObject


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
3. Reason about each identified problem to make sure they are valid. Have in mind, your review must be consistent with the source code in Firefox. As valid comments, not related to the patch under analysis now, consider some below:
[
    {{
        "file": "com/br/main/Pressure.java",
        "code_line": 458,
        "comment" : "In the third code block, you are using `nsAutoStringN<256>` instead of `nsString`. This is a good change as `nsAutoStringN<256>` is more efficient for small strings. However, you should ensure that the size of `tempString` does not exceed 256 characters, as `nsAutoStringN<256>` has a fixed size."
    }},
    {{
        "file": "com/pt/main/atp/Texture.cpp",
        "code_line": 620,
        "comment" : "The `filterAAR` function inside `#updateAllowAllRequestRules()` is created every time the method is called. Consider defining this function outside of the method to avoid unnecessary function creation."
    }},
    {{
        "file": "drs/control/Statistics.cpp",
        "code_line": 25,
        "comment" : "The condition in the `if` statement is a bit complex and could be simplified for better readability. Consider extracting `!Components.isSuccessCode(status) && blockList.includes(ChromeUtils.getXPCOMErrorName(status))` into a separate function with a descriptive name, such as `isBlockedError`."
    }}
]
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

PROMPT_TEMPLATE_SIMPLE_REVIEW = """ Review the following C++ patch:

{patch}
"""

class ReviewRequest:
    patch_id: int

    def __init__(self, patch_id) -> None:
        super().__init__()
        self.patch_id = patch_id


class Patch:
    raw_diff: str
    
    def __init__(self, raw_diff, file_diff=None) -> None:
        super().__init__()
        self.raw_diff = raw_diff
        self.file_diff = file_diff


class ReviewData(ABC):
    @abstractmethod
    def get_review_request_by_id(self, review_id: int) -> ReviewRequest:
        raise NotImplementedError

    @abstractmethod
    def get_patch_by_id(self, patch_id: int) -> Patch:
        raise NotImplementedError


class PhabricatorReviewData(ReviewData):
    def __init__(self):
        phabricator.set_api_key(
            get_secret("PHABRICATOR_URL"), get_secret("PHABRICATOR_TOKEN")
        )

    def get_review_request_by_id(self, revision_id: int) -> ReviewRequest:
        revisions = phabricator.get(rev_ids=[int(revision_id)])
        assert len(revisions) == 1
        return ReviewRequest(revisions[0]["fields"]["diffID"])

    def get_patch_by_id(self, patch_id: int) -> Patch:
        assert phabricator.PHABRICATOR_API is not None
        raw_diff = phabricator.PHABRICATOR_API.load_raw_diff(int(patch_id))
        return Patch(raw_diff)

class SwarmReviewData(ReviewData):
    def __init__(self):
        self.auth = {'user':get_secret('SWARM_USER'),'password':get_secret('SWARM_PASS'),'port':get_secret('SWARM_PORT'),'instance':get_secret('INSTANCE')}

    # return ReviewRequest object with patch_id = revision_id
    def get_review_request_by_id(self, revision_id: int) -> ReviewRequest:
        return ReviewRequest(revision_id)

    # return rawdiff from the initial version of the revision with id patch_id
    def get_patch_by_id(self, patch_id: int) -> Patch:
        revisions = swarm.get(self.auth, rev_ids=[int(patch_id)], version_l = [0, 1])
        assert len(revisions) == 1
        return Patch(revisions[0]["fields"]["diff"])
    
    # return rawdiff from the initial version of a specific version of the revision_id
    def get_patch_by_version_fromto(self, revision_id: int, version_before: int =0, version_num: int = 1) -> Patch:
        revisions = swarm.get(self.auth, rev_ids=[int(revision_id)], version_l = [version_before, version_num])
        assert len(revisions) == 1
        return Patch(revisions[0]["fields"]["diff"], revisions[0]["fields"]["file_diff"])

review_data_classes = {
    "phabricator": PhabricatorReviewData,
    "swarm": SwarmReviewData,
}

@dataclass
class InlineComment:
    filename: str
    start_line: int
    end_line: int
    comment: str
    on_added_code: bool


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

class CodeReviewTool(GenerativeModelTool):
    version = "0.0.1"

    def __init__(self, rag, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)

        self.summarization_chain = LLMChain(
            prompt=PromptTemplate.from_template(PROMPT_TEMPLATE_SUMMARIZATION),
            llm=self.llm,
        )
        self.filtering_chain = LLMChain(
            prompt=PromptTemplate.from_template(PROMPT_TEMPLATE_FILTERING_ANALYSIS),
            llm=self.llm,
        )
        
        self.rag = rag
        
    def run(self, patch: Patch) -> list[InlineComment] | None:
        patch_set = PatchSet.from_string(patch.raw_diff)
        formatted_patch = format_patch_set(patch_set)
        if formatted_patch == "":
            return None

        output_summarization = self.summarization_chain.invoke(
            {"patch": formatted_patch},
            return_only_outputs=True,
        )["text"]

        print(output_summarization)

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

        output = conversation_chain.predict(
            input=PROMPT_TEMPLATE_REVIEW.format(patch=formatted_patch)
        )

        print(output)

        memory.clear()

        raw_output = self.filtering_chain.invoke(
            {"review": output, "patch": patch.raw_diff},
            return_only_outputs=True,
        )["text"]

        return raw_output


    def run_rag(self, patch: Patch) -> list[InlineComment] | None:
        assert self.rag is not None
        
        rag_examples = self.rag.get_examples(patch.file_diff)
        
        patch_set = PatchSet.from_string(patch.raw_diff)
        formatted_patch = format_patch_set(patch_set)
        if formatted_patch == "":
            return None

        output_summarization = self.summarization_chain.invoke(
            {"patch": formatted_patch},
            return_only_outputs=True,
        )["text"]

        print(output_summarization)

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
        
        memory.save_context(
            {
                "input":PROMPT_TEMPLATE_REVIEW.format(patch='')
            },
            {"output": 'No, problem, I will apply those rules.'},
        )
        
        for ex in rag_examples:
            memory.save_context(
                {
                    "input":PROMPT_TEMPLATE_SIMPLE_REVIEW.format(patch=ex[1]['diff'])
                },
                {"output": f"[{ex[1]['info_dir']}]"},
            )
            
        output = conversation_chain.predict(
            input=PROMPT_TEMPLATE_SIMPLE_REVIEW.format(patch=formatted_patch)
        )

        memory.clear()

        raw_output = self.filtering_chain.invoke(
            {"review": output, "patch": patch.raw_diff},
            return_only_outputs=True,
        )["text"]

        return raw_output