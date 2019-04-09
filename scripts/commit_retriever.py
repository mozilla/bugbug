# -*- coding: utf-8 -*-

import argparse
import lzma
import os
import shutil
from datetime import datetime
from logging import INFO, basicConfig, getLogger

import hglib
from dateutil.relativedelta import relativedelta

from bugbug import repository

basicConfig(level=INFO)
logger = getLogger(__name__)


class Retriever(object):
    def __init__(self, cache_root):
        self.cache_root = cache_root

        assert os.path.isdir(cache_root), f"Cache root {cache_root} is not a dir."
        self.repo_dir = os.path.join(cache_root, "mozilla-central")

    def retrieve_commits(self):
        shared_dir = self.repo_dir + "-shared"
        cmd = hglib.util.cmdbuilder(
            "robustcheckout",
            "https://hg.mozilla.org/mozilla-central",
            self.repo_dir,
            purge=True,
            sharebase=shared_dir,
            networkattempts=7,
            branch=b"tip",
        )

        cmd.insert(0, hglib.HGPATH)

        proc = hglib.util.popen(cmd)
        out, err = proc.communicate()
        if proc.returncode:
            raise hglib.error.CommandError(cmd, proc.returncode, out, err)

        logger.info("mozilla-central cloned")

        two_years_and_six_months_ago = datetime.utcnow() - relativedelta(
            years=2, months=6
        )
        repository.download_commits(self.repo_dir, two_years_and_six_months_ago)

        logger.info("commit data extracted from repository")

        self.compress_file("data/commits.json")

    def compress_file(self, path):
        with open(path, "rb") as input_f:
            with lzma.open(f"{path}.xz", "wb") as output_f:
                shutil.copyfileobj(input_f, output_f)


def main():
    description = "Retrieve and extract the information from Mozilla-Central repository"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("cache-root", help="Cache for repository clones.")

    args = parser.parse_args()

    retriever = Retriever(getattr(args, "cache-root"))

    retriever.retrieve_commits()
