import argparse
import logging

from bugbug import generative_model_tool
from bugbug.tools.release_notes import ReleaseNotesCommitsSelector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Generate Firefox release notes.")
    parser.add_argument("--version", required=True, help="Target version identifier")
    parser.add_argument(
        "--chunk-size", type=int, default=100, help="Number of commits per chunk"
    )
    parser.add_argument(
        "--llm", default="openai", help="Model to use for summarization"
    )

    args = parser.parse_args()
    llm = generative_model_tool.create_llm_from_args(args)

    selector = ReleaseNotesCommitsSelector(chunk_size=args.chunk_size, llm=llm)
    results = selector.get_final_release_notes_commits(version=args.version)
    print(results)


if __name__ == "__main__":
    main()
