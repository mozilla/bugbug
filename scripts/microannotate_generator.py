# -*- coding: utf-8 -*-

import argparse
import concurrent.futures
import os
import subprocess
from logging import INFO, basicConfig, getLogger

from microannotate import generator

from bugbug import repository
from bugbug.utils import get_secret, retry

basicConfig(level=INFO)
logger = getLogger(__name__)


TOTAL_COMMITS = 20000
STEPS = 20


class MicroannotateGenerator(object):
    def __init__(self, cache_root, repo_url, tokenize, remove_comments):
        self.cache_root = cache_root
        self.repo_url = repo_url
        self.tokenize = tokenize
        self.remove_comments = remove_comments

        assert os.path.isdir(cache_root), f"Cache root {cache_root} is not a dir."
        self.repo_dir = os.path.join(cache_root, "mozilla-central")

    def generate(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            cloner = executor.submit(repository.clone, self.repo_dir)
            cloner.add_done_callback(
                lambda future: logger.info("mozilla-central cloned")
            )

            git_user = get_secret("GIT_USER")
            git_password = get_secret("GIT_PASSWORD")

            repo_push_url = self.repo_url.replace(
                "https://", f"https://{git_user}:{git_password}@"
            )
            git_repo_path = os.path.basename(self.repo_url)

            executor.submit(self.clone_git_repo, git_repo_path)

        retry(
            lambda: subprocess.run(
                ["git", "config", "--global", "http.postBuffer", "12M"], check=True
            )
        )

        for i in range(STEPS):
            logger.info(f"Step {i} out of {STEPS}")

            done = generator.generate(
                self.repo_dir,
                git_repo_path,
                limit=TOTAL_COMMITS // STEPS,
                tokenize=self.tokenize,
                remove_comments=self.remove_comments,
            )

            with open("done", "w") as f:
                f.write(str(1 if done else 0))

            retry(
                lambda: subprocess.run(
                    ["git", "push", repo_push_url, "master"],
                    cwd=git_repo_path,
                    check=True,
                )
            )

            if done:
                break

    def clone_git_repo(self, git_repo_path):
        retry(
            lambda: subprocess.run(
                ["git", "clone", self.repo_url, git_repo_path], check=True
            )
        )

        try:
            retry(
                lambda: subprocess.run(
                    ["git", "pull", self.repo_url, "master"],
                    cwd=git_repo_path,
                    capture_output=True,
                    check=True,
                )
            )
        except subprocess.CalledProcessError as e:
            # When the repo is empty.
            if b"Couldn't find remote ref master" in e.stdout:
                pass


def main():
    description = "Generate a mirror git repository where content is split by word"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("cache-root", help="Cache for repository clones.")
    parser.add_argument("repo-url", help="Mirror repository URL.")
    parser.add_argument(
        "--tokenize", help="Enable word-level tokenization.", action="store_true"
    )
    parser.add_argument(
        "--remove-comments", help="Enable comment removal.", action="store_true"
    )

    args = parser.parse_args()

    generator = MicroannotateGenerator(
        getattr(args, "cache-root"),
        getattr(args, "repo-url"),
        args.tokenize,
        args.remove_comments,
    )

    generator.generate()


if __name__ == "__main__":
    main()
