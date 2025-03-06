import argparse
import logging
import os

from langchain_openai import ChatOpenAI

from bugbug.tools.release_notes import ReleaseNotesCommitsSelector

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Generate Firefox release notes.")
    parser.add_argument("--version", required=True, help="Target version identifier")
    parser.add_argument(
        "--chunk-size", type=int, default=10000, help="Chunk size for token processing"
    )
    parser.add_argument(
        "--llm", default="openai-gpt-4o", help="Model to use for summarization"
    )

    args = parser.parse_args()

    if args.llm.startswith("openai-"):
        model_name = args.llm.replace("openai-", "")
        llm = ChatOpenAI(
            model=model_name,
            temperature=0.1,
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {args.llm}")

    selector = ReleaseNotesCommitsSelector(chunk_size=args.chunk_size, llm=llm)
    results = selector.select_worthy_commits(version=args.version)
    print(results)


if __name__ == "__main__":
    main()
