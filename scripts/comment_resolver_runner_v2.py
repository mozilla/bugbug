import argparse
import logging
import os

from dotenv import load_dotenv
from openai import OpenAI

from bugbug import generative_model_tool
from bugbug.tools.comment_resolver_v2 import CodeGeneratorTool

client = OpenAI(
    api_key=os.environ.get("OPENAI_API_KEY"),
)
model = "gpt-4o"


def run(args, llm) -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    llm_tool = CodeGeneratorTool(
        client=client, model=model, hunk_size=args.hunk_size, llm=llm
    )
    generated_fix = llm_tool.generate_fix(
        revision_id=args.revision_id,
        diff_id=args.diff_id,
        comment_id=args.comment_id,
    )

    print(generated_fix)
    return


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--revision-id",
        type=int,
        help="Revision ID that the comment was made on (on Phabricator).",
    )
    parser.add_argument(
        "--diff-id",
        type=int,
        help="Diff ID that the comment was made on (on Phabricator).",
    )
    parser.add_argument(
        "--comment-id",
        type=int,
        help="Comment ID (on Phabricator).",
    )
    parser.add_argument(
        "--hunk-size",
        type=int,
        help="+/- <HUNK-SIZE> lines (from the raw file content) around the comment to include in the prompt.",
    )
    generative_model_tool.create_llm_to_args(parser)
    args = parser.parse_args()
    llm = generative_model_tool.create_llm_from_args(args)
    run(args, llm)
