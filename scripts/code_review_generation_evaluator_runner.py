import argparse
import logging
import sys

from dotenv import load_dotenv

from bugbug.generative_model_tool import create_llm
from bugbug.tools.code_review_generation import (
    FixCommentDB,
    LocalQdrantVectorDB,
)
from bugbug.tools.code_review_generation_evaluator import (
    CodeGeneratorEvaluatorTool,
    conduct_evaluation,
)


def run(args) -> None:
    load_dotenv()

    logging.basicConfig(level=logging.INFO)

    db = FixCommentDB(LocalQdrantVectorDB(collection_name="fix_comments"))

    llm = create_llm(args.llm)
    llm_tool = CodeGeneratorEvaluatorTool(llm=llm, db=db)

    input_csv = args.input_csv
    output_csv = args.output_csv

    conduct_evaluation(input_csv, output_csv, llm_tool)


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--llm",
        help="LLM",
        choices=["openai"],
        default="openai",
    )
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

    return parser.parse_args(args)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    run(args)
