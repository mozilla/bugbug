# -*- coding: utf-8 -*-

import argparse
import itertools
import os
from logging import INFO, basicConfig, getLogger

import hglib

from bugbug import db, repository
from bugbug.utils import create_tar_zst, zstd_compress

basicConfig(level=INFO)
logger = getLogger(__name__)


class Retriever(object):
    def __init__(self, cache_root: str) -> None:
        assert os.path.isdir(cache_root), f"Cache root {cache_root} is not a dir."
        self.repo_dir = os.path.join(cache_root, "mozilla-central")

    def retrieve_commits(self, limit: int | None) -> None:
        repository.clone(self.repo_dir)

        rev_start: int | str
        if limit:
            # Mercurial revset supports negative integers starting from tip
            rev_start = -limit
        else:
            db.download(repository.COMMITS_DB, support_files_too=True)

            # Start after the last commit of the DB, not the last filtered one, or the commits after it
            # that are filtered out (e.g. backouts) would be mined (and added to the DB) again.
            last_commit = repository.get_last_commit(
                include_no_bug=True, include_backouts=True, include_ignored=True
            )
            rev_start = (
                f"children({last_commit['node']})" if last_commit is not None else 0
            )

        with hglib.open(self.repo_dir) as hg:
            revs = repository.get_revs(hg, rev_start)

        chunk_size = 70000

        for chunk in itertools.batched(revs, chunk_size, strict=False):
            repository.download_commits(self.repo_dir, revs=list(chunk))

        logger.info("commit data extracted from repository")

        # Some commits that were already in the DB from the previous run might need
        # to be updated (e.g. coverage information, backouts).
        repository.update_commits(self.repo_dir)

        zstd_compress(repository.COMMITS_DB)
        create_tar_zst(os.path.join("data", repository.COMMIT_EXPERIENCES_DB))


def main() -> None:
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
