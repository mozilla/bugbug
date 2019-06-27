# -*- coding: utf-8 -*-

import argparse
import os
import subprocess
from logging import INFO, basicConfig, getLogger

import hglib
from microannotate import generator

from bugbug.utils import get_secret, retry

basicConfig(level=INFO)
logger = getLogger(__name__)


class MicroannotateGenerator(object):
    def __init__(self, cache_root):
        self.cache_root = cache_root

        assert os.path.isdir(cache_root), f"Cache root {cache_root} is not a dir."
        self.repo_dir = os.path.join(cache_root, "mozilla-central")

    def generate(self):
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

        git_user = get_secret("GIT_USER")
        git_password = get_secret("GIT_PASSWORD")

        repo_url = "https://github.com/marco-c/gecko-dev-wordified"
        repo_push_url = (
            f"https://{git_user}:{git_password}@github.com/marco-c/gecko-dev-wordified"
        )
        git_repo_path = os.path.basename(repo_url)

        retry(
            lambda: subprocess.run(
                ["git", "clone", repo_url, git_repo_path], check=True
            )
        )

        try:
            retry(
                lambda: subprocess.run(
                    ["git", "pull", repo_url, "master"],
                    cwd=git_repo_path,
                    capture_output=True,
                    check=True,
                )
            )
        except subprocess.CalledProcessError as e:
            # When the repo is empty.
            if b"Couldn't find remote ref master" in e.stdout:
                pass

        generator.generate(self.repo_dir, git_repo_path, limit=10000)

        retry(
            lambda: subprocess.run(
                ["git", "config", "--global", "http.postBuffer", "12M"], check=True
            )
        )
        retry(
            lambda: subprocess.run(
                ["git", "push", repo_push_url, "master"], cwd=git_repo_path, check=True
            )
        )


def main():
    description = "Generate a mirror git repository where content is split by word"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("cache-root", help="Cache for repository clones.")

    args = parser.parse_args()

    generator = MicroannotateGenerator(getattr(args, "cache-root"))

    generator.generate()
