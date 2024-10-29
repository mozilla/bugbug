import csv
import json
import logging
import re
from types import SimpleNamespace

from langchain_openai import OpenAIEmbeddings
from libmozdata.phabricator import PhabricatorAPI
from qdrant_client import QdrantClient

from bugbug.generative_model_tool import GenerativeModelTool
from bugbug.phabricator import fetch_diff_from_url
from bugbug.tools.code_review import PhabricatorReviewData
from bugbug.utils import get_secret
from bugbug.vectordb import QdrantVectorDB, VectorPoint

review_data = PhabricatorReviewData()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
api = PhabricatorAPI(get_secret("PHABRICATOR_TOKEN"))


class LocalQdrantVectorDB(QdrantVectorDB):
    def __init__(self, collection_name: str, location: str = "http://localhost:6333"):
        self.collection_name = collection_name
        self.client = QdrantClient(location=location)

    def setup(self):
        super().setup()

    def delete_collection(self):
        self.client.delete_collection(self.collection_name)


class FixCommentDB:
    def __init__(self, db: LocalQdrantVectorDB):
        self.db = db
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-large", api_key=get_secret("OPENAI_API_KEY")
        )

    def line_to_vector_point(self, line: str):
        data = json.loads(line)
        comment_content = data["comment"]["content"]

        embedding = self.embeddings.embed_query(comment_content)

        vector_point = VectorPoint(
            id=data["comment"]["id"],
            vector=embedding,
            payload={"comment": comment_content, "fix_info": data},
        )
        return vector_point

    def upload_dataset(self, dataset_file: str):
        with open(dataset_file, "r") as f:
            points = []
            for line in f:
                vector_point = self.line_to_vector_point(line)
                points.append(vector_point)
            self.db.insert(points)

    def search_similar_comments(
        self, comment_content: str, revision_id: int, diff_length_limit: int, top_n: int
    ):
        query_embedding = self.embeddings.embed_query(comment_content)
        results = self.db.search(query_embedding)
        similar_comments = []

        for result in results:
            if (
                result.payload["fix_info"]["revision_id"] != revision_id
                and len(result.payload["fix_info"]["fix_patch_diff"])
                < diff_length_limit
            ):
                similar_comments.append(
                    (result.payload["comment"], result.payload["fix_info"])
                )

                if len(similar_comments) >= top_n:
                    break

        return similar_comments if similar_comments else None


class CodeGeneratorTool(GenerativeModelTool):
    version = "0.0.1"

    def __init__(
        self,
        llm,
        db: FixCommentDB,
    ) -> None:
        self.db = db
        self.llm = llm

    def run(self, prompt: str):
        messages = [("system", "You are a code review bot."), ("user", prompt)]
        response = self.llm.invoke(messages)
        return response.content

    def generate_fix(
        self,
        comment,
        relevant_diff,
        prompt_type,
        hunk_size,
        similar_comments_and_fix_infos,
        evaluation,
        generated_fix,
    ):
        if not evaluation:
            prompt = generate_prompt(
                comment.content,
                relevant_diff,
                comment.start_line,
                comment.end_line,
                similar_comments_and_fix_infos,
                prompt_type,
                hunk_size,
            )
        else:
            prompt = f"""
            Comment: {comment.content}
            Diff (before fix): {relevant_diff}
            Generated Fix: {generated_fix}

            Does the generated fix address the comment correctly? Answer YES or NO, followed by a very short and succinct explanation. It is considered a valid fix if the generated fix CONTAINS a fix for the comment despite having extra unnecessary fluff addressing other stuff.
            """

        generated_fix = self.run(prompt=prompt)
        return generated_fix


class CodeGeneratorEvaluatorTool(GenerativeModelTool):
    version = "0.0.1"

    def __init__(self, llm, db) -> None:
        self.db = db
        self.llm = llm

    def run(self, prompt: str):
        messages = [
            (
                "system",
                "You are an evaluator of code generation to address review comments.",
            ),
            ("user", prompt),
        ]
        response = self.llm.invoke(messages)
        return response.content

    def generate_fix(self, comment, relevant_diff, generated_fix):
        prompt = f"""
        Comment: {comment}
        Diff (before fix): {relevant_diff}
        Generated Fix: {generated_fix}

        Does the generated fix address the comment correctly? Answer YES or NO, followed by a very short and succinct explanation. It is considered a valid fix if the generated fix CONTAINS a fix for the comment despite having extra unnecessary fluff addressing other stuff.
        """
        qualitative_feedback = self.run(prompt=prompt)
        return qualitative_feedback


def fetch_patch_diff(patch_id):
    diffs = api.search_diffs(diff_id=patch_id)
    if diffs:
        return diffs
    else:
        logger.error(f"No diffs found for patch ID: {patch_id}")
        return None


def extract_relevant_diff(patch_diff, filename, start_line, end_line, hunk_size):
    file_diff_pattern = rf"diff --git a/{re.escape(filename)} b/{re.escape(filename)}\n.*?(?=\ndiff --git|$)"
    match = re.search(file_diff_pattern, patch_diff, re.DOTALL)

    if match:
        hunk_header_pattern = r"@@ -(\d+),(\d+) \+(\d+),(\d+) @@"
        match2 = re.finditer(hunk_header_pattern, match.group(0))
        first_index = None
        last_index = None

        for m in match2:
            diff_lines = match.group(0).split("\n")

            deletion_start_line = int(m.group(1))
            deletion_num_lines = int(m.group(2))
            addition_start_line = int(m.group(3))
            addition_num_lines = int(m.group(4))

            if (
                start_line < deletion_start_line and start_line < addition_start_line
            ) or (
                start_line > (deletion_start_line + deletion_num_lines)
                and start_line > (addition_start_line + addition_num_lines)
            ):
                continue

            added_lines = []
            deleted_lines = []

            for line in diff_lines[diff_lines.index(m.group()) + 1 :]:
                if line.startswith("-"):
                    deleted_lines.append(line)
                elif line.startswith("+"):
                    added_lines.append(line)

            if not deleted_lines or not added_lines:
                logger.error(f"No deleted or added lines found for file: {filename}")
                return None

            deletion_start_diff_line = deleted_lines[
                min(
                    len(deleted_lines) - 1,
                    max(0, start_line - deletion_start_line - hunk_size),
                )
            ]
            deletion_end_diff_line = deleted_lines[
                max(
                    0,
                    min(
                        len(deleted_lines) - 1,
                        end_line - deletion_start_line + hunk_size,
                    ),
                )
            ]

            addition_start_diff_line = added_lines[
                min(
                    len(added_lines) - 1,
                    max(0, start_line - addition_start_line - hunk_size),
                )
            ]
            addition_end_diff_line = added_lines[
                max(
                    0,
                    min(
                        len(added_lines) - 1, end_line - addition_start_line + hunk_size
                    ),
                )
            ]

            first_index = None
            last_index = None

            diff_lines = match.group(0).split("\n")

            for i, line in enumerate(diff_lines):
                if line in [
                    deletion_start_diff_line,
                    deletion_end_diff_line,
                    addition_start_diff_line,
                    addition_end_diff_line,
                ]:
                    if first_index is None:
                        first_index = i
                    last_index = i

        if first_index is not None and last_index is not None:
            relevant_diff = "\n".join(diff_lines[first_index : last_index + 1])
            return relevant_diff
        else:
            logger.error(f"No relevant diff found for lines: {start_line}-{end_line}")
            return None
    else:
        logger.error(f"No diff found for file: {filename}")
        return None


def get_revision_id_from_patch(patch_id):
    diffs = api.search_diffs(diff_id=patch_id)

    if diffs:
        revision_phid = diffs[0]["revisionPHID"]

        revision = api.load_revision(rev_phid=revision_phid)

        return revision["id"]
    else:
        logger.error(f"No diffs found for patch ID: {patch_id}")
        return None


def generate_prompt(
    comment_content,
    relevant_diff,
    start_line,
    end_line,
    similar_comments_and_fix_infos,
    prompt_type,
    hunk_size,
):
    if prompt_type == "zero-shot":
        prompt = f"""
        CONTEXT:
        You are a code review bot that generates fixes in code given an inline review comment.
        You will be provided with the COMMENT, the LINE NUMBERS the comment is referring to,
        and the relevant DIFF for the file affected. Your goal is to generate a code fix based
        on the COMMENT, LINE NUMBERS, and DIFF provided, and nothing more. Generate ONLY the
        lines you are adding/deleting, indicated by + and -. For example, if you are modifying
        a single line, show that you are deleting (-) the line from the original diff and adding
        (+) the fixed line. The line numbers help to contextualize the changes within the diff.
        ONLY address the comment. Do not make any other changes.

        COMMENT:
        "{comment_content}"

        LINE NUMBERS:
        {start_line}-{end_line}

        DIFF:
        ```
        {relevant_diff}
        ```

        FIX:
        """
    if prompt_type == "single-shot":
        similar_comment, fix_info = similar_comments_and_fix_infos[0]

        example_initial_diff = fetch_diff_from_url(
            fix_info["revision_id"], fix_info["initial_patch_id"], single_patch=True
        )
        example_relevant_initial_diff = extract_relevant_diff(
            example_initial_diff,
            fix_info["comment"]["filename"],
            fix_info["comment"]["start_line"],
            fix_info["comment"]["end_line"],
            hunk_size,
        )

        example_relevant_fix_diff = extract_relevant_diff(
            fix_info["fix_patch_diff"],
            fix_info["comment"]["filename"],
            fix_info["comment"]["start_line"],
            fix_info["comment"]["end_line"],
            hunk_size,
        )

        prompt = f"""
        CONTEXT:
        You are a code review bot that generates fixes in code given an inline review comment.
        You will be provided with the COMMENT, the LINE NUMBERS the comment is referring to,
        and the relevant DIFF for the file affected. Your goal is to generate a code fix based
        on the COMMENT, LINE NUMBERS, and DIFF provided, and nothing more. Generate ONLY the
        lines you are adding/deleting, indicated by + and -. For example, if you are modifying
        a single line, show that you are deleting (-) the line from the original diff and adding
        (+) the fixed line. The line numbers help to contextualize the changes within the diff.
        An EXAMPLE has been provided for your reference. ONLY address the comment. Do not make
        any other changes.

        EXAMPLE:
        COMMENT:
        "{similar_comment}"

        LINE NUMBERS:
        {fix_info["comment"]["start_line"]}-{fix_info["comment"]["end_line"]}

        DIFF:
        ```
        {example_relevant_initial_diff}
        ```

        FIX:
        ```
        {example_relevant_fix_diff}
        ```

        YOUR TURN:
        COMMENT:
        "{comment_content}"

        LINE NUMBERS:
        {start_line}-{end_line}

        DIFF:
        ```
        {relevant_diff}
        ```

        FIX:
        """
    if prompt_type == "chain-of-thought":
        prompt = f"""
        CONTEXT:
        You are a code review bot that generates fixes in code based on an inline review comment.
        You will be provided with the COMMENT, the LINE NUMBERS the comment is referring to,
        and the relevant DIFF for the affected file. Your goal is to carefully analyze the COMMENT,
        LINE NUMBERS, and DIFF provided, and generate a code fix accordingly. Only make changes
        directly relevant to the feedback.

        THINKING PROCESS:
        1. **Understand the COMMENT**: Carefully read the comment to grasp the reviewer’s intention.
        2. **Locate the Relevant Lines**: Use the provided LINE NUMBERS to pinpoint the exact lines
           in the DIFF that need modification.
        3. **Analyze the DIFF**: Review the current state of the code in the DIFF to understand
           what is currently implemented.
        4. **Determine Necessary Changes**: Based on the COMMENT, decide what needs to be added,
           modified, or removed in the code. Focus on addressing the feedback without introducing
           unnecessary changes.
        5. **Generate the FIX**: Output the exact lines you are adding or deleting, using + and -
           symbols to indicate modifications. For example, if a line is being modified, show it as
           being removed (-) and then the corrected line as being added (+). ONLY address the comment.
           Do not make any other changes.

        COMMENT:
        "{comment_content}"

        LINE NUMBERS:
        {start_line}-{end_line}

        DIFF:
        ```
        {relevant_diff}
        ```

        FIX:
        """

    if prompt_type == "multi-shot":
        examples = ""
        for similar_comment, fix_info in similar_comments_and_fix_infos:
            example_initial_diff = fetch_diff_from_url(
                fix_info["revision_id"], fix_info["initial_patch_id"], single_patch=True
            )
            example_relevant_initial_diff = extract_relevant_diff(
                example_initial_diff,
                fix_info["comment"]["filename"],
                fix_info["comment"]["start_line"],
                fix_info["comment"]["end_line"],
                hunk_size,
            )
            example_relevant_fix_diff = extract_relevant_diff(
                fix_info["fix_patch_diff"],
                fix_info["comment"]["filename"],
                fix_info["comment"]["start_line"],
                fix_info["comment"]["end_line"],
                hunk_size,
            )
            examples += f"""
            EXAMPLE:
            COMMENT:
            "{similar_comment}"

            LINE NUMBERS:
            {fix_info["comment"]["start_line"]}-{fix_info["comment"]["end_line"]}

            DIFF:
            ```
            {example_relevant_initial_diff}
            ```


            FIX:
            {example_relevant_fix_diff}
            """

        prompt = f"""
        CONTEXT:
        You are a code review bot that generates fixes in code given an inline review comment.
        You will be provided with the COMMENT, the LINE NUMBERS the comment is referring to,
        and the relevant DIFF for the file affected. Your goal is to generate a code fix based
        on the COMMENT, LINE NUMBERS, and DIFF provided, and nothing more. Generate ONLY the
        lines you are adding/deleting, indicated by + and -. For example, if you are modifying
        a single line, show that you are deleting (-) the line from the original diff and adding
        (+) the fixed line. The line numbers help to contextualize the changes within the diff.
        Two EXAMPLES has been provided for your reference. ONLY address the comment. Do not make
        any other changes.

        EXAMPLES:
        {examples}

        YOUR TURN:
        COMMENT:
        "{comment_content}"

        LINE NUMBERS:
        {start_line}-{end_line}

        DIFF:
        ```
        {relevant_diff}
        ```

        FIX:
        """

    return prompt


def generate_fixes(
    llm_tool,
    db,
    generation_limit,
    diff_length_limits,
    prompt_types,
    hunk_sizes,
    output_csv,
):
    counter = 0
    revision_ids = extract_revision_id_list_from_dataset("data/fixed_comments.json")

    with open(output_csv, mode="w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            [
                "Revision ID",
                "Patch ID",
                "Prompt Type",
                "Length Limit",
                "Hunk Size",
                "Comment Length",
                "Generated Code Length",
                "File Path",
                "Comment",
                "Start Line",
                "End Line",
                "Relevant Diff",
                "Generated Fix",
            ]
        )

        for i, (patch_id, comments) in enumerate(
            review_data.get_all_inline_comments(lambda c: True)
        ):
            revision_id = get_revision_id_from_patch(patch_id)

            if not revision_id:
                logger.error(f"Skipping Patch ID {patch_id} as no revision ID found.")
                continue

            if revision_id not in revision_ids:
                logger.error(
                    f"Skipping Patch ID {patch_id} as revision ID {revision_id} not in dataset."
                )
                continue

            diff = fetch_diff_from_url(revision_id, patch_id, single_patch=True)

            if not diff:
                logger.error(f"Skipping Patch ID {patch_id} as no diff found.")
                continue

            for comment in comments:
                if counter >= generation_limit:
                    return

                for hunk_size in hunk_sizes:
                    if counter >= generation_limit:
                        break

                    filename = comment.filename
                    relevant_diff = extract_relevant_diff(
                        diff, filename, comment.start_line, comment.end_line, hunk_size
                    )

                    if relevant_diff:
                        for prompt_type in prompt_types:
                            if counter >= generation_limit:
                                break

                            for diff_length_limit in diff_length_limits:
                                if counter >= generation_limit:
                                    break

                                similar_comments_and_fix_infos = (
                                    db.search_similar_comments(
                                        comment.content,
                                        revision_id,
                                        diff_length_limit,
                                        2,
                                    )
                                )

                                if similar_comments_and_fix_infos is None:
                                    logger.info(
                                        f"No similar comment found for comment: {comment.content}"
                                    )
                                    continue

                                generated_fix = llm_tool.generate_fix(
                                    comment,
                                    relevant_diff,
                                    prompt_type,
                                    hunk_size,
                                    similar_comments_and_fix_infos,
                                    False,
                                    None,
                                )

                                comment_length = len(comment.content)
                                generated_code_length = len(generated_fix)
                                file_path = filename

                                writer.writerow(
                                    [
                                        revision_id,
                                        patch_id,
                                        prompt_type,
                                        diff_length_limit,
                                        hunk_size,
                                        comment_length,
                                        generated_code_length,
                                        file_path,
                                        comment.content,
                                        comment.start_line,
                                        comment.end_line,
                                        relevant_diff,
                                        generated_fix,
                                    ]
                                )

                                counter += 1

                    else:
                        print(f"No relevant diff found for Comment ID {comment.id}.\n")


def extract_revision_id_list_from_dataset(dataset_file):
    revision_ids = []

    with open(dataset_file, "r") as f:
        for line in f:
            data = json.loads(line)
            revision_ids.append(data["revision_id"])

    return revision_ids


def generate_individual_fix(llm_tool, db, revision_id, diff_id, comment_id):
    revision_details = api.load_revision(rev_id=revision_id)
    revision_phid = revision_details["phid"]
    transactions = api.request("transaction.search", objectIdentifier=revision_phid)

    target_comment = {}

    found = False

    for transaction in transactions["data"]:
        if transaction["type"] == "inline":
            for comment in transaction["comments"]:
                if comment["id"] == comment_id:
                    target_comment["filepath"] = transaction["fields"]["path"]
                    target_comment["content"] = comment["content"]
                    target_comment["start_line"] = transaction["fields"]["line"]
                    target_comment["end_line"] = (
                        transaction["fields"]["line"] + transaction["fields"]["length"]
                    )
                    found = True
                    break
        if found:
            break

    target_comment = SimpleNamespace(**target_comment)

    diff = fetch_diff_from_url(revision_id, diff_id, single_patch=True)
    relevant_diff = extract_relevant_diff(
        diff,
        target_comment.filepath,
        target_comment.start_line,
        target_comment.end_line,
        hunk_size=100,
    )

    generated_fix = llm_tool.generate_fix(
        target_comment,
        relevant_diff,
        prompt_type="zero-shot",
        hunk_size=100,
        similar_comments_and_fix_infos=None,
        evaluation=False,
        generated_fix=None,
    )

    print(generated_fix)
