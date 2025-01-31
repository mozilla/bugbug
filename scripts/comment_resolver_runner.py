import argparse
import csv
import json
import logging
import sys

from dotenv import load_dotenv

import bugbug.db as db
import bugbug.phabricator as phabricator
from bugbug.generative_model_tool import create_llm_from_args
from bugbug.tools.comment_resolver import (
    CodeGeneratorTool,
    FixCommentDB,
    LocalQdrantVectorDB,
    generate_fixes,
    generate_individual_fix,
)


def run(args) -> None:
    load_dotenv()

    logging.basicConfig(level=logging.INFO)

    db = FixCommentDB(LocalQdrantVectorDB(collection_name="fix_comments"))

    if args.create_db:
        db.db.delete_collection()
        db.db.setup()
        db.upload_dataset(args.dataset_file)

    llm = create_llm_from_args(args)
    llm_tool = CodeGeneratorTool(llm=llm, db=db)

    if (
        args.revision_id
        and args.diff_id
        and args.comment_id
        and not args.use_db_examples
    ):
        generate_individual_fix(
            llm_tool=llm_tool,
            db=db,
            revision_id=args.revision_id,
            diff_id=args.diff_id,
            comment_id=args.comment_id,
        )
    elif not args.use_db_examples:
        generate_fixes(
            llm_tool=llm_tool,
            db=db,
            generation_limit=args.generation_limit,
            prompt_types=args.prompt_types,
            hunk_sizes=args.hunk_sizes,
            diff_length_limits=args.diff_length_limits,
            output_csv=args.output_csv,
            single_comment=args.single_comment,
        )
    else:
        with open(
            "data/fixed_comments4.json", "r", encoding="utf-8"
        ) as json_file, open(
            "output.csv", "w", newline="", encoding="utf-8"
        ) as csv_file:
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(
                [
                    "bug_id",
                    "revision_id",
                    "comment_id",
                    "comment_content",
                    "initial_patch_id",
                    "final_patch_id",
                    "fix_patch_diff",
                    "generated_fix",
                ]
            )

            for line in json_file:
                try:
                    data = json.loads(line.strip())
                    bug_id = data.get("bug_id")
                    revision_id = data.get("revision_id")
                    comment_id = data.get("comment", {}).get("id")
                    comment_content = data.get("comment", {}).get("content")
                    initial_patch_id = data.get("initial_patch_id")
                    final_patch_id = data.get("final_patch_id")
                    fix_patch_diff = data.get("fix_patch_diff")
                    generated_fix = generate_individual_fix(
                        llm_tool=llm_tool,
                        db=db,
                        revision_id=revision_id,
                        diff_id=initial_patch_id,
                        comment_id=comment_id,
                    )

                    csv_writer.writerow(
                        [
                            bug_id,
                            revision_id,
                            comment_id,
                            comment_content,
                            initial_patch_id,
                            final_patch_id,
                            fix_patch_diff,
                            generated_fix,
                        ]
                    )

                except json.JSONDecodeError as e:
                    print(f"Error parsing JSON: {e}")


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--llm",
        help="LLM",
        choices=["openai"],
        default="openai",
    )
    parser.add_argument(
        "--create-db",
        action="store_true",
        help="If set, the local Qdrant database will be created and populated.",
    )
    parser.add_argument(
        "--dataset-file",
        type=str,
        default="data/fixed_comments.json",
        help="Dataset file to upload as Qdrant database.",
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default="code_generations.csv",
        help="Output CSV file for results.",
    )
    parser.add_argument(
        "--prompt-types",
        nargs="+",
        default=["zero-shot"],
        help="Types of prompts to use.",
    )
    parser.add_argument(
        "--diff-length-limits",
        nargs="+",
        type=int,
        default=[1000],
        help="Diff length limits to enforce when searching for examples.",
    )
    parser.add_argument(
        "--hunk-sizes",
        nargs="+",
        type=int,
        default=[20],
        help="Hunk sizes to enforce when searching for examples.",
    )
    parser.add_argument(
        "--generation-limit",
        type=int,
        default=100,
        help="Maximum number of generations.",
    )
    parser.add_argument(
        "--single-comment",
        action="store_true",
        help="If set, only patches with single comments will be included.",
    )
    parser.add_argument(
        "--revision-id",
        type=int,
        help="Revision ID for individual fix generation.",
    )
    parser.add_argument(
        "--diff-id",
        type=int,
        help="Diff ID for individual fix generation.",
    )
    parser.add_argument(
        "--comment-id",
        type=int,
        help="Comment ID for individual fix generation.",
    )
    parser.add_argument(
        "--use-db-examples",
        action="store_true",
        help="If set, the database examples will be used for generation.",
    )

    return parser.parse_args(args)


if __name__ == "__main__":
    db.download(phabricator.FIXED_COMMENTS_DB)
    args = parse_args(sys.argv[1:])
    run(args)
