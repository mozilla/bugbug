# -*- coding: utf-8 -*-

import argparse
import os
from logging import INFO, basicConfig, getLogger

import hglib

from bugbug import db, repository
from bugbug.utils import create_tar_zst, zstd_compress

basicConfig(level=INFO)
logger = getLogger(__name__)


class Retriever(object):
    def __init__(self, cache_root):
        assert os.path.isdir(cache_root), f"Cache root {cache_root} is not a dir."
        self.repo_dir = os.path.join(cache_root, "mozilla-central")

    def retrieve_commits(self, limit):
        repository.clone(self.repo_dir)

        if limit:
            # Mercurial revset supports negative integers starting from tip
            rev_start = -limit
        else:
            db.download(repository.COMMITS_DB, support_files_too=True)

            rev_start = 0
            for commit in repository.get_commits():
                rev_start = f"children({commit['node']})"

        with hglib.open(self.repo_dir) as hg:
            revs = repository.get_revs(hg, rev_start)

        chunk_size = 70000

        for i in range(0, len(revs), chunk_size):
            repository.download_commits(self.repo_dir, revs=revs[i : (i + chunk_size)])

        logger.info("commit data extracted from repository")

        # Some commits that were already in the DB from the previous run might need
        # to be updated (e.g. coverage information).
        repository.update_commits()

        zstd_compress(repository.COMMITS_DB)
        create_tar_zst(os.path.join("data", repository.COMMIT_EXPERIENCES_DB))


def main():
    description = "Retrieve and extract the information from Mozilla-Central repository"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument(
        "--limit",
        type=int,
        help="Only download the N oldest commits, used mainly for integration tests",
    )
    parser.add_argument("cache-root", help="Cache for repository clones.")

    args = parser.parse_args()

    retriever = Retriever(getattr(args, "cache-root"))

    retriever.retrieve_commits(args.limit)


if __name__ == "__main__":
    main()
