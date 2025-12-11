import csv
import json
import os
import subprocess
from datetime import datetime, timedelta

import tiktoken
from dateutil import parser, tz
from langchain.chains.conversation.base import ConversationChain
from langchain.chains.llm import LLMChain
from langchain.memory import ConversationBufferMemory
from langchain_core.prompts import PromptTemplate
from langchain_openai import ChatOpenAI
from unidiff import PatchSet

import bugbug.bugzilla as bugzilla

csv.field_size_limit(10**8)

### VARIABLES

# For the input file, we consider the list of reported bugs available here: https://github.com/mozilla/regressors-regressions-dataset
# Clone the repository locally and inform the path to the file dataset.csv
INPUT_FILE = ""

# As we need to access some additional information associated with the commits reported in the INPUT_FILE, we need to have locally the central repository.
# For that, you might clone the repo locally and inform its path below: https://hg-edge.mozilla.org/mozilla-central
LOCAL_MERCURIAL_PATH = ""
REPORT_DIRECTORY = ""
REPORT_FILENAME_GPT = "filtered_comment_gpt.csv"
REPORT_FILENAME_DEEPSEEK = "filtered_comment_deepseek.csv"

OPEN_API_KEY = ""
OPEN_AI_MODEL = "gpt-4o-mini"
DEEPSEEK_API = ""
DEEPSEEK_MODEL_NAME = "deepseek-chat"
TEMPERATURE = 0.2

### PROMPTS

CODE_SUMMARIZATION_DIFF = """
You are an expert reviewer for source code with extensive experience in analyzing and summarizing code changes.

The bug associated with patch_bug was introduced and later fixed. Below, you can find further information about the fix.
Fix title: {fix_title}
Fix description: {fix_description}

Your task:
Analyze the provided code and generate a concise summary focusing on the exact changes in patch_bug that introduced the issue and how patch_fix resolved it. Ignore any modifications unrelated to the bug fix.

You must report:
1. The root cause of the issue in `patch_bug`: Identify the specific code lines in patch_bug responsible for the bug. Report the exact affected line and explain why they led to the issue. One single line number for change.
2. The specific changes in `patch_fix` that correct the issue: Explain how the bug was resolved, but keep the focus on mapping fixes back to the faulty lines in `patch_bug`.

Output Format:
Provide a structured response that explicitly maps faulty lines in `patch_bug` to the fix in `patch_fix`, like this:

{{
    "root_cause": {{
        "filename": "<file_path>",
        "line": [<line_number>],
        "explanation": "<Why these lines introduced the bug>"
    }},
    "fix": {{
        "filename": "<file_path>",
        "line": [<line_number>],
        "explanation": "<How these changes in patch_fix resolved the issue>"
    }}
}}

Bug commit message: {bug_commit_message}
{patch_bug}

Fix commit message: {fix_commit_message}
{patch_fix}
    """

FILTERING_COMMENTS = """
You are an expert reviewer with extensive experience in source code reviews.

Please analyze the comments below and filter out any comments that are not related to the changes applied in the commit diff.

Apply the following filters:
1. Remove comments that focus on documentation, comments, error handling, or requests for tests.
2. Remove comments that suggest developers to double-check or ensure their implementations (e.g., verifying the existence, initialization, or creation of objects, methods, or files) without providing actionable feedback.
3. Remove comments that are purely descriptive and do not suggest improvements or highlight problems.
4. Remove comments that are solely praising (e.g., "This is a good addition to the code.").
5. Consolidate duplicate comments that address the same issue into a single, comprehensive comment.
6. Do not change the contents of the comments.

Output:
Return a single JSON file containing the valid comments, and no additional content.
Ensure the output format matches the example below:

Example:
```json
[
    {{
        "filename": "netwerk/streamconv/converters/mozTXTToHTMLConv.cpp",
        "start_line": 1211,
        "content": "Ensure that the size of `tempString` does not exceed 256 characters. Using `nsAutoStringN<256>` is efficient for small strings, but exceeding the size can lead to buffer issues.",
        "label": "code validation"
        "label_justification": "Functional - Validation"
    }}
]

Below, you can find the comments:
{comments}

And now, you can find the commit diff:
{bug_summarization}
"""

CODE_GEN_BUG_FIX = """
Now, you're asked to generate code review comments for `patch_bug`, aiming to avoid the occurrence of the reported bug.

### Guidelines:
1. **Objective**: Identify changes in `patch_bug` that introduced the bug and provide actionable feedback to prevent it.
2. **Reference**: Use `bug_summarization` to understand the bug’s cause, but ensure that all comments apply strictly to `patch_bug`.
3. **Exclusions**:
   - Do **not** comment on changes that appear only in `bug_summarization` but were not present in `patch_bug`.
   - Do **not** suggest fixes based on changes made in `bug_summarization`. The goal is to improve `patch_bug` to prevent the issue from occurring.
4. **Context**: Align your review with the issues raised in `bug_summarization` and Mozilla's source code guidelines.
5. **Format**: Write comments in the following JSON format, considering the `patch_bug` information:

   ```json
   [
       {{
           \"filename\": \"<file_path>\",
           \"start_line\": <line_number>,
           \"content\": \"<comment_content>\",
           \"label\": \"<label>\",
           \"label_justification\": \"<label_justification>\"
       }}
   ]
   ```
6. **Content of Comments**:
   - Be concise, comments should be short and to the point.
   - Provide actionable feedback that would lide the the fixes from the Patch fixing the bug.
   - Each comment should focus solely on potential issues introduced by the added '+' lines of code.
   - Avoid referencing any external descriptions (like issue tracking tickets or patches fixing the bug). Focus on the Patch introducing the bug itself.
   - The comment type could be:
        Categories and Subcategories
        1. Readability:
        Focus: Making the code easier to read and understand.
        Subcategories include:
            * Refactoring - Consistency: Uniform coding styles and practices.
            * Refactoring - Naming Convention: Clear, descriptive identifiers.
            * Refactoring - Readability: General clarity improvements.
            * Refactoring - Simplification: Reducing unnecessary complexity.
            * Refactoring - Visual Representation: Improving code layout and formatting.
        2. Design and Maintainability:
        Focus: Improving structure and long-term upkeep.
        Subcategories include:
            * Discussion - Design discussion: Architectural or structural decisions.
            * Functional - Support: Adding or enhancing support functionality.
            * Refactoring - Alternate Output: Changing what the code returns or prints.
            * Refactoring - Code Duplication: Removing repeated code.
            * Refactoring - Code Simplification: Streamlining logic.
            * Refactoring - Magic Numbers: Replacing hard-coded values with named constants.
            * Refactoring - Organization of the code: Logical structuring of code.
            * Refactoring - Solution approach: Rethinking problem-solving approaches.
            * Refactoring - Unused Variables: Removing variables not in use.
            * Refactoring - Variable Declarations: Improving how variables are declared or initialized.
        3. Performance:
        Focus: Making the code faster or more efficient.
        Subcategories include:
            * Functional - Performance: General performance improvements.
            * Functional - Performance Optimization: Specific performance-focused refactoring.
            * Functional - Performance and Safety: Balancing speed and reliability.
            * Functional - Resource: Efficient use of memory, CPU, etc.
            * Refactoring - Performance Optimization: Improving performance through code changes.
        4. Defect:
        Focus: Fixing bugs and potential issues.
        Subcategories include:
            * Functional - Conditional Compilation
            * Functional - Consistency and Thread Safety
            * Functional - Error Handling
            * Functional - Exception Handling
            * Functional - Initialization
            * Functional - Interface
            * Functional - Lambda Usage
            * Functional - Logical
            * Functional - Null Handling
            * Functional - Security
            * Functional - Serialization
            * Functional - Syntax
            * Functional - Timing
            * Functional - Type Safety
            * Functional - Validation
        5. Other:
        Use only if none of the above apply:
        Subcategories include:
            * None of the above
            * Does not apply
    - Keep It Focused: Limit your comments to the issues that could lead to problems identified by the Jira ticket and are directly related to the changes made in the Patch fixing the bug.
7. **Limit the Comments**: Write as little amount of comments possible.

### Steps:
1. Analyze the summary of changes from `bug_summarization` and `patch_bug`.
2. Identify lines in `patch_bug` that could have introduced the bug described in `bug_summarization`.
3. Do **not** suggest fixes based on changes in `bug_summarization`. Instead, focus on how `patch_bug` could be improved to avoid the bug.
4. Exclude comments for changes unrelated to the bug.
5. Write actionable and concise comments, focusing strictly on code changes in `patch_bug`, using the JSON format.
6. **Final Check**: Ensure that each comment refers to a line in `patch_bug`, not the changes described in `bug_summarization`.

### Example:

```json
[
    {{
        \"filename\": \"netwerk/streamconv/converters/mozTXTToHTMLConv.cpp\",
        \"start_line\": 1211,
        \"content\": \"The lack of input validation in this line could lead to an unexpected crash. Consider validating `tempString` length before using it.\",
        \"label\": \"Defect\",
        \"label_justification\": \"Functional - Validation\"
    }}
]
```

Below, you can find the `patch_bug`:
{patch_bug}

And now, you can find the `bug_summarization`:
{bug_summarization}
"""


def filter_comments_using_deepseek(gen_comments, formatted_patch_fix):
    deepseek_llm = ChatOpenAI(
        model=DEEPSEEK_MODEL_NAME,
        temperature=TEMPERATURE,
        openai_api_base="https://api.deepseek.com/v1",
        openai_api_key=DEEPSEEK_API,
    )

    filtering = LLMChain(
        prompt=PromptTemplate.from_template(FILTERING_COMMENTS), llm=deepseek_llm
    )

    filtered_comments = filtering.invoke(
        {"bug_summarization": formatted_patch_fix, "comments": gen_comments},
    )["text"]

    return filtered_comments


def filter_comments_using_gpt(formatted_patch_fix, gen_comments, llm):
    filtering = LLMChain(
        prompt=PromptTemplate.from_template(FILTERING_COMMENTS), llm=llm
    )
    filtered_comments_gpt = filtering.invoke(
        {"bug_summarization": formatted_patch_fix, "comments": gen_comments},
    )["text"]
    return filtered_comments_gpt


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


def format_patch_set_filtered_files(patch_set, patch_fix):
    modified_files = []

    for patch_fix in patch_fix.modified_files:
        modified_files.append(patch_fix.path)

    output = ""
    for patch in patch_set:
        for hunk in patch:
            if patch.path in modified_files:
                output += f"Filename: {patch.target_file}\n"
                output += f"{get_hunk_with_associated_lines(hunk)}\n"

    return output


def target_file_is_changed_by_bug_and_fix_commits(patch_bug, patch_fix):
    for changed_file_fix in patch_fix.modified_files:
        for changed_file_bug in patch_bug.modified_files:
            if (
                changed_file_bug.is_binary_file is False
                and changed_file_fix.is_binary_file is False
                and changed_file_bug.path == changed_file_fix.path
            ):
                return True
    return False


def generate_code_review_comments(
    patch_bug,
    patch_fix,
    bug_commit_message,
    fix_commit_message,
    bug_title,
    fix_title,
    bug_description,
    fix_description,
):
    patch_set_bug = PatchSet.from_string(patch_bug)
    formatted_patch_bug = format_patch_set(patch_set_bug)

    if formatted_patch_bug == "":
        return None

    patch_set_fix = PatchSet.from_string(patch_fix)
    formatted_patch_fix = format_patch_set(patch_set_fix)

    if (
        target_file_is_changed_by_bug_and_fix_commits(patch_set_bug, patch_set_fix)
        is False
    ):
        return None

    if formatted_patch_fix == "":
        return None

    formatted_patch_bug = format_patch_set_filtered_files(patch_set_bug, patch_set_fix)
    if formatted_patch_bug == "":
        return None

    llm = ChatOpenAI(
        model_name=OPEN_AI_MODEL, temperature=TEMPERATURE, openai_api_key=OPEN_API_KEY
    )

    summarization_chain = LLMChain(
        prompt=PromptTemplate.from_template(CODE_SUMMARIZATION_DIFF), llm=llm
    )

    buffer = ConversationBufferMemory()
    conversation_chain = ConversationChain(
        llm=llm,
        memory=buffer,
    )

    output_summarization = summarization_chain.invoke(
        {
            "patch_bug": formatted_patch_bug,
            "bug_commit_message": bug_commit_message,
            "patch_fix": formatted_patch_fix,
            "fix_commit_message": fix_commit_message,
            "bug_title": bug_title,
            "bug_description": bug_description,
            "fix_title": fix_title,
            "fix_description": fix_description,
        },
    )["text"]

    buffer.save_context(
        {
            "input": "You are an expert reviewer for source code, with experience on source code reviews."
        },
        {"output": "Sure, I can certainly assist with source code reviews."},
    )

    gen_comments = conversation_chain.predict(
        input=CODE_GEN_BUG_FIX.format(
            patch_fix=formatted_patch_fix,
            patch_bug=formatted_patch_bug,
            bug_summarization=output_summarization,
        )
    )

    filtered_comments_gpt = filter_comments_using_gpt(
        formatted_patch_fix, gen_comments, llm
    )

    filtered_comments_deepseek = filter_comments_using_deepseek(
        gen_comments, formatted_patch_fix
    )

    return [filtered_comments_gpt, filtered_comments_deepseek]


def save_output_comments(
    bug_id,
    bug_commit,
    bug_tokens,
    fix_id,
    fix_commit,
    fix_tokens,
    bug_summary,
    comments_json,
    interval_bug_fix,
    filename,
):
    output_csv_path = os.path.join(REPORT_DIRECTORY, filename)
    try:
        if isinstance(comments_json, str):
            comments = json.loads(comments_json)
        else:
            comments = comments_json

        headers = [
            "Bug ID",
            "Bug Commit",
            "TokensBug",
            "Fix ID",
            "Fix Commit",
            "TokensFix",
            "Bug Summary",
            "Interval Bug-Fix",
            "Filename",
            "Start Line",
            "Comment Content",
            "Label",
            "Justification",
        ]

        if len(comments) > 0:
            if not os.path.exists(output_csv_path):
                with open(
                    output_csv_path, mode="w", newline="", encoding="utf-8"
                ) as csv_file:
                    writer = csv.DictWriter(csv_file, fieldnames=headers)
                    writer.writeheader()
                csv_file.close()

            with open(
                output_csv_path, mode="a+", newline="", encoding="utf-8"
            ) as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=headers)

                for comment in comments:
                    writer.writerow(
                        {
                            "Bug ID": bug_id,
                            "Bug Commit": bug_commit,
                            "TokensBug": bug_tokens,
                            "Fix ID": fix_id,
                            "Fix Commit": fix_commit,
                            "TokensFix": fix_tokens,
                            "Bug Summary": bug_summary,
                            "Interval Bug-Fix": interval_bug_fix,
                            "Filename": comment.get("filename", ""),
                            "Start Line": comment.get("start_line", ""),
                            "Comment Content": comment.get("content", ""),
                            "Label": comment.get("label", ""),
                            "Justification": comment.get("label_justification", ""),
                        }
                    )
            print(f"CSV file has been successfully written to {output_csv_path}.")
    except Exception as e:
        print(f"An error occurred: {e}")


def get_diff(commit, repo_path):
    try:
        cmd = ["hg", "diff", "-c", commit]

        result = subprocess.run(
            cmd,
            cwd=repo_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        if result.returncode != 0:
            print(f"Error: {result.stderr}")
            return None

        return result.stdout

    except Exception as e:
        print(f"An error occurred: {e}")
        return None


def count_openai_tokens(log, model="gpt-4"):
    model_to_encoding = {
        "gpt-3.5-turbo": "cl100k_base",
        "gpt-4": "cl100k_base",
        "davinci": "p50k_base",
        "curie": "p50k_base",
        "babbage": "p50k_base",
        "ada": "p50k_base",
    }

    encoding_name = model_to_encoding.get(model)
    if not encoding_name:
        raise ValueError(f"Unsupported model: {model}")

    enc = tiktoken.get_encoding(encoding_name)

    try:
        tokens = enc.encode(log)
        return len(tokens)
    except Exception as e:
        print(e)
        return 50000


def extract_and_parse_json(input_string):
    try:
        start_index = input_string.find("[")
        end_index = input_string.rfind("]")

        if start_index == -1 or end_index == -1 or start_index > end_index:
            raise ValueError("Invalid JSON format: Missing or misaligned brackets.")

        json_content = input_string[start_index : end_index + 1]

        parsed_json = json.loads(json_content)
        return parsed_json
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON: {e}")


def get_commit_date(commit_hash, repo_path="."):
    try:
        result = subprocess.run(
            ["hg", "log", "-r", commit_hash, "--template", "{date|isodate}"],
            cwd=repo_path,
            text=True,
            capture_output=True,
            check=True,
        )
        commit_date_str = result.stdout.strip()
        commit_date = parser.parse(commit_date_str)
        return commit_date
    except subprocess.CalledProcessError as e:
        print(f"Error retrieving commit date: {e.stderr}")
        return None
    except ValueError as e:
        print(f"Error parsing date: {e}")
        return None


def get_commit_message(repo_path, commit_hash):
    command = ["hg", "log", "-r", commit_hash, "--template", "{desc}"]

    result = subprocess.run(
        command,
        cwd=repo_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise Exception(f"Error retrieving commit message: {result.stderr}")

    return result.stdout.strip()


def is_commit_done_within_the_last_target_years(commit_date, years):
    now = datetime.now(tz=tz.tzlocal())
    years_ago = now - timedelta(days=years * 365)
    return commit_date >= years_ago


if __name__ == "__main__":
    with open(INPUT_FILE, mode="r", newline="", encoding="utf-8") as file:
        csv_reader = list(csv.reader(file))  # Read all lines into a list

        for line in csv_reader[2:]:
            repo_path = LOCAL_MERCURIAL_PATH

            if (
                len(line[1].split(" ")) < 2
                and len(line[4].split(" ")) < 2
                and line[1] != ""
                and line[4] != ""
            ):
                fix_commit_hash = line[1]
                fix_commit_date = get_commit_date(fix_commit_hash, repo_path)

                if fix_commit_date is not None:
                    if is_commit_done_within_the_last_target_years(fix_commit_date, 10):
                        bug_commit_diff = get_diff(line[4], repo_path)
                        fix_commit_diff = get_diff(line[1], repo_path)

                        bug_count_tokens = count_openai_tokens(bug_commit_diff)
                        fix_count_tokens = count_openai_tokens(fix_commit_diff)

                        if (
                            (fix_count_tokens < 4000)
                            and (len(line[3].split(" ")) < 2)
                            and (line[3] != "")
                        ):
                            bug_id = line[3]
                            bug_mozilla = bugzilla.get(bug_id)
                            bug_commit_hash = line[4]
                            bug_commit_message = get_commit_message(
                                repo_path, bug_commit_hash
                            )

                            fix_id = line[0]
                            fix_mozilla = bugzilla.get(fix_id)
                            fix_commit_message = get_commit_message(
                                repo_path, fix_commit_hash
                            )

                            bug_commit_date = get_commit_date(
                                bug_commit_hash, repo_path
                            )

                            interval_bug_fix = (fix_commit_date - bug_commit_date).days

                            bug_patch_title = bug_mozilla.get(int(bug_id))["summary"]
                            bug_summary = bug_mozilla.get(int(bug_id))["comments"][0][
                                "text"
                            ]

                            fix_patch_title = fix_mozilla.get(int(fix_id))["summary"]
                            fix_summary = fix_mozilla.get(int(fix_id))["comments"][0][
                                "text"
                            ]

                            generated_comments = generate_code_review_comments(
                                bug_commit_diff,
                                fix_commit_diff,
                                bug_commit_message,
                                fix_commit_message,
                                bug_patch_title,
                                fix_patch_title,
                                bug_summary,
                                fix_summary,
                            )

                            if (
                                generated_comments is not None
                                and len(generated_comments) > 1
                            ):
                                gpt_filtered_comments = generated_comments[0]
                                deepseek_filtered_comments = generated_comments[1]

                                if (
                                    gpt_filtered_comments is not None
                                    and deepseek_filtered_comments is not None
                                ):
                                    if gpt_filtered_comments is not None:
                                        valid_json = extract_and_parse_json(
                                            gpt_filtered_comments
                                        )
                                        save_output_comments(
                                            bug_id,
                                            bug_commit_hash,
                                            bug_count_tokens,
                                            fix_id,
                                            fix_commit_hash,
                                            fix_count_tokens,
                                            bug_summary,
                                            valid_json,
                                            interval_bug_fix,
                                            REPORT_FILENAME_GPT,
                                        )
                                    if deepseek_filtered_comments is not None:
                                        valid_json = extract_and_parse_json(
                                            deepseek_filtered_comments
                                        )
                                        save_output_comments(
                                            bug_id,
                                            bug_commit_hash,
                                            bug_count_tokens,
                                            fix_id,
                                            fix_commit_hash,
                                            fix_count_tokens,
                                            bug_summary,
                                            valid_json,
                                            interval_bug_fix,
                                            REPORT_FILENAME_DEEPSEEK,
                                        )

                                else:
                                    print("No comments were generated.")
                        else:
                            print("The commit patch is too large.")
                    else:
                        print(
                            "The commit was NOT made within the last target years ("
                            + str(fix_commit_date)
                            + ")."
                        )
                else:
                    print(
                        "Could not retrieve the commit date for "
                        + str(fix_commit_hash)
                        + "."
                    )
            else:
                print(
                    "Skipping scenario as bug is associated with multiple commits and fixes"
                )
