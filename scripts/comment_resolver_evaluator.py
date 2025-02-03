import argparse
import csv
import json
import logging
import sys

from dotenv import load_dotenv

from bugbug.generative_model_tool import create_llm_from_args
from bugbug.tools.comment_resolver import (
    CodeGeneratorEvaluatorTool,
    FixCommentDB,
    LocalQdrantVectorDB,
)


def find_fix_in_dataset(revision_id, initial_patch_id, dataset_file):
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

    return {"precision": precision, "recall": recall, "f1": f1}


def compare_fixes(revision_id, initial_patch_id, generated_fix, reference_fix):
    if reference_fix:
        metrics = calculate_metrics(reference_fix, generated_fix)
        return metrics
    else:
        logging.info(
            f"No matching fix found in the dataset for Revision {revision_id} and Patch {initial_patch_id}."
        )
        return None


def conduct_evaluation(input_csv, output_csv, llm_tool, equivalent_fix):
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
                comment,
                relevant_diff,
                generated_fix,
                reference_fix,
                True,
                equivalent_fix,
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


def validate_fix_with_llm(
    comment_content, relevant_diff, generated_fix, fix_patch_diff, llm_tool, patch=None
):
    return llm_tool.generate_fix(
        comment=comment_content,
        relevant_diff=relevant_diff,
        generated_fix=generated_fix,
        actual_fix=fix_patch_diff,
        new_prompt=True,
    )


def run(args) -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)

    db = FixCommentDB(LocalQdrantVectorDB(collection_name="fix_comments"))
    llm = create_llm_from_args(args)
    llm_tool = CodeGeneratorEvaluatorTool(llm=llm, db=db)

    if args.llm_compare_method:
        with open("output.csv", "r", encoding="utf-8") as input_csv_file, open(
            "output2.csv", "w", newline="", encoding="utf-8"
        ) as output_csv_file:
            csv_reader = csv.reader(input_csv_file)
            csv_writer = csv.writer(output_csv_file)

            header = next(csv_reader)
            header.append("llm_comparison_output")
            csv_writer.writerow(header)

            for row in csv_reader:
                (
                    bug_id,
                    revision_id,
                    comment_id,
                    comment_content,
                    relevant_diff,
                    initial_patch_id,
                    final_patch_id,
                    fix_patch_diff,
                    generated_fix,
                ) = row
                result = validate_fix_with_llm(
                    comment_content,
                    relevant_diff,
                    generated_fix,
                    fix_patch_diff,
                    llm_tool,
                )
                row.append(result)
                csv_writer.writerow(row)
    else:
        input_csv = args.input_csv
        output_csv = args.output_csv
        equivalent_fix = args.equivalent_fix

        conduct_evaluation(input_csv, output_csv, llm_tool, equivalent_fix)


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", help="LLM", choices=["openai"], default="openai")
    parser.add_argument(
        "--input-csv",
        type=str,
        default="code_generations.csv",
        help="Input CSV file from the generation script.",
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default="evaluated_code_generations.csv",
        help="Output CSV file for results.",
    )
    parser.add_argument(
        "--equivalent-fix",
        action="store_true",
        help="If set, the prompt will check if both the generated and reference fixes are strictly equivalent, otherwise if it is a subset.",
    )
    parser.add_argument(
        "--llm-compare-method",
        action="store_true",
        help="If set, the prompt will compare the generated fix with the reference fix using the LLM.",
    )
    return parser.parse_args(args)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    run(args)
