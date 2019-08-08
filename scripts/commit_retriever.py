# -*- coding: utf-8 -*-

import argparse
import os
import pickle
from logging import INFO, basicConfig, getLogger

from bugbug import db, repository
from bugbug.utils import zstd_compress

basicConfig(level=INFO)
logger = getLogger(__name__)


class Retriever(object):
    def __init__(self, cache_root):
        self.cache_root = cache_root

        assert os.path.isdir(cache_root), f"Cache root {cache_root} is not a dir."
        self.repo_dir = os.path.join(cache_root, "mozilla-central")

    def retrieve_commits(self):
        repository.clone(self.repo_dir)

        if not db.exists(repository.COMMITS_DB):
            repository.COMMITS_DB = "data/commits.json"
            db.register(
                repository.COMMITS_DB,
                "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/commits.json.zst",
                2,
                ["commit_experiences.pickle.zst"],
            )

        if not db.is_old_version(repository.COMMITS_DB):
            db.download(repository.COMMITS_DB, support_files_too=True)

            for commit in repository.get_commits():
                pass

            rev_start = f"children({commit['node']})"
        else:
            rev_start = 0

        repository.download_commits(self.repo_dir, rev_start)

        logger.info("commit data extracted from repository")

        if repository.COMMITS_DB == "data/commits.json":
            with open("data/commits.pickle", "wb") as f:
                for commit in repository.get_commits():
                    pickle.dump(commit, f)
            repository.COMMITS_DB = "data/commits.pickle"

        zstd_compress(repository.COMMITS_DB)
        zstd_compress("data/commit_experiences.pickle")


def main():
    description = "Retrieve and extract the information from Mozilla-Central repository"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument(
        "--limit",
        type=int,
        help="Only download the N oldest commits, used mainly for integration tests",
    )  # TODO: Use limit
    parser.add_argument("cache-root", help="Cache for repository clones.")

    args = parser.parse_args()

    retriever = Retriever(getattr(args, "cache-root"))

    retriever.retrieve_commits()


if __name__ == "__main__":
    main()
