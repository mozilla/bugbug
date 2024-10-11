import csv
import json
import logging

from libmozdata.phabricator import PhabricatorAPI

from bugbug.generative_model_tool import GenerativeModelTool, create_llm
from bugbug.tools.code_review import PhabricatorReviewData
from bugbug.tools.code_review_generation import FixCommentDB, LocalQdrantVectorDB
from bugbug.utils import get_secret

review_data = PhabricatorReviewData()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
api = PhabricatorAPI(get_secret("PHABRICATOR_TOKEN"))


class CodeGeneratorEvaluatorTool(GenerativeModelTool):
    version = "0.0.1"

    def __init__(
        self,
        llm,
        db,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(llm, *args, **kwargs)
        self.db = db

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

    def generate_fix(
        self,
        comment,
        relevant_diff,
        generated_fix,
    ):
        prompt = f"""
        Comment: {comment}
        Diff (before fix): {relevant_diff}
        Generated Fix: {generated_fix}

        Does the generated fix address the comment correctly? Answer YES or NO, followed by a very short and succinct explanation. It is considered a valid fix if the generated fix CONTAINS a fix for the comment despite having extra unnecessary fluff addressing other stuff.
        """

        qualitative_feedback = self.run(prompt=prompt)
        return qualitative_feedback


def find_fix_in_dataset(
    revision_id,
    initial_patch_id,
    dataset_file,
):
    with open(dataset_file, "r") as f:
        for line in f:
            data = json.loads(line)
            if data["revision_id"] == int(revision_id) and data[
                "initial_patch_id"
            ] == int(initial_patch_id):
                return data["fix_patch_diff"]
    return None


def calculate_metrics(reference_fix, generated_fix):
    reference_tokens = reference_fix.split()
    generated_tokens = generated_fix.split()

    common_tokens = set(reference_tokens) & set(generated_tokens)
    precision = len(common_tokens) / len(generated_tokens) if generated_tokens else 0
    recall = len(common_tokens) / len(reference_tokens) if reference_tokens else 0
    f1 = (2 * precision * recall) / (precision + recall) if (precision + recall) else 0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def compare_fixes(revision_id, initial_patch_id, generated_fix, reference_fix):
    if reference_fix:
        metrics = calculate_metrics(reference_fix, generated_fix)
        return metrics
    else:
        print(
            f"No matching fix found in the dataset for Revision {revision_id} and Patch {initial_patch_id}."
        )
        return None


def conduct_evaluation(input_csv, output_csv, llm_tool):
    with open(input_csv, "r") as infile, open(
        output_csv, mode="w", newline=""
    ) as outfile:
        reader = csv.DictReader(infile)

        fieldnames = reader.fieldnames + [
            "Reference Fix",
            "Precision",
            "Recall",
            "F1",
            "Qualitative Feedback",
        ]
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()

        for row in reader:
            revision_id = row["Revision ID"]
            initial_patch_id = row["Patch ID"]
            generated_fix = row["Generated Fix"]
            comment = row["Comment"]
            relevant_diff = row["Relevant Diff"]

            reference_fix = find_fix_in_dataset(
                revision_id=revision_id,
                initial_patch_id=initial_patch_id,
                dataset_file="data/fixed_comments.json",
            )

            metrics = compare_fixes(
                revision_id=revision_id,
                initial_patch_id=initial_patch_id,
                generated_fix=generated_fix,
                reference_fix=reference_fix,
            )

            qualitative_feedback = llm_tool.generate_fix(
                comment, relevant_diff, generated_fix
            )

            if metrics is not None:
                writer.writerow(
                    {
                        **row,
                        "Reference Fix": reference_fix,
                        "Precision": metrics["precision"],
                        "Recall": metrics["recall"],
                        "F1": metrics["f1"],
                        "Qualitative Feedback": qualitative_feedback,
                    }
                )


def main():
    db = FixCommentDB(LocalQdrantVectorDB(collection_name="fix_comments"))
    llm = create_llm("openai")
    llm_tool = CodeGeneratorEvaluatorTool(llm=llm, db=db)

    input_csv = "metrics_results.csv"
    output_csv = "metrics_results_evaluated.csv"

    conduct_evaluation(input_csv, output_csv, llm_tool)


if __name__ == "__main__":
    main()
