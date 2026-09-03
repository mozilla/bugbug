import argparse
import sys

from langchain.chat_models import init_chat_model

from bugbug.tools.comment_resolution.agent import CodeGeneratorTool
from bugbug.tools.core.llms import DEFAULT_OPENAI_MODEL


def run(args) -> None:
    llm = init_chat_model(DEFAULT_OPENAI_MODEL)
    codegen = CodeGeneratorTool(
        client=None,
        model="gpt-4",
        hunk_size=10,
        llm=llm,
    )
    result = codegen.generate_fixes_for_all_comments(int(args.revision_id))
    print(result)


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--revision_id",
        type=int,
        required=True,
        help="The revision ID to process.",
    )
    return parser.parse_args(args)


if __name__ == "__main__":
    args = parse_args(sys.argv[1:])
    run(args)
