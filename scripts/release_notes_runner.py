import argparse
import logging

from bugbug.tools.release_notes import ReleaseNotesGenerator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Generate Firefox release notes.")
    parser.add_argument("--version", required=True, help="Target version identifier")
    parser.add_argument(
        "--chunk-size", type=int, default=10000, help="Chunk size for token processing"
    )
    parser.add_argument(
        "--model", default="gpt-4o", help="Model to use for summarization"
    )

    args = parser.parse_args()

    generator = ReleaseNotesGenerator(chunk_size=args.chunk_size, model=args.model)
    generator.generate_worthy_commits(version=args.version)


if __name__ == "__main__":
    main()
