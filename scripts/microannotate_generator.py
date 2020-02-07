# -*- coding: utf-8 -*-

import argparse
import os
import subprocess
from logging import INFO, basicConfig, getLogger

import tenacity
from microannotate import generator

from bugbug import db, repository
from bugbug.utils import ThreadPoolExecutorResult, get_secret

basicConfig(level=INFO)
logger = getLogger(__name__)


# When updating the version, the git repositories will be recreated from scratch.
# This is useful when new meaningful versions of rust-code-analysis or microannotate
# are used.
VERSION = 2
COMMITS_STEP = 5000


class MicroannotateGenerator(object):
    def __init__(self, cache_root, repo_url, tokenize, remove_comments):
        self.cache_root = cache_root
        self.repo_url = repo_url
        self.git_repo_path = os.path.basename(self.repo_url)
        self.tokenize = tokenize
        self.remove_comments = remove_comments

        assert os.path.isdir(cache_root), f"Cache root {cache_root} is not a dir."
        self.repo_dir = os.path.join(cache_root, "mozilla-central")

    def generate(self):
        db_path = os.path.join("data", self.git_repo_path)
        db.register(
            db_path,
            f"https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.microannotate_{self.git_repo_path}.latest/artifacts/public/",
            VERSION,
        )

        # TODO: Check the version again once we can run tasks for longer (https://bugzilla.mozilla.org/show_bug.cgi?id=1604175).
        is_old_version = False  # db.is_old_schema(db_path)

        with ThreadPoolExecutorResult(max_workers=2) as executor:
            cloner = executor.submit(repository.clone, self.repo_dir)
            cloner.add_done_callback(
                lambda future: logger.info("mozilla-central cloned")
            )

            git_user = get_secret("GIT_USER")
            git_password = get_secret("GIT_PASSWORD")

            repo_push_url = self.repo_url.replace(
                "https://", f"https://{git_user}:{git_password}@"
            )

            if not is_old_version:
                executor.submit(self.clone_git_repo)
            else:
                executor.submit(self.init_git_repo)

        tenacity.retry(
            lambda: subprocess.run(
                ["git", "config", "--global", "http.postBuffer", "12M"], check=True
            ),
            wait=tenacity.wait_fixed(30),
            stop=tenacity.stop_after_attempt(5),
        )()

        push_args = ["git", "push", repo_push_url, "master"]
        if is_old_version:
            push_args.append("--force")

        done = False
        while not done:
            done = generator.generate(
                self.repo_dir,
                self.git_repo_path,
                limit=COMMITS_STEP,
                tokenize=self.tokenize,
                remove_comments=self.remove_comments,
            )

            tenacity.retry(
                lambda: subprocess.run(push_args, cwd=self.git_repo_path, check=True),
                wait=tenacity.wait_fixed(30),
                stop=tenacity.stop_after_attempt(5),
            )()

    def init_git_repo(self):
        subprocess.run(["git", "init", self.git_repo_path], check=True)

        subprocess.run(
            ["git", "remote", "add", "origin", self.repo_url],
            cwd=self.git_repo_path,
            check=True,
        )

    def clone_git_repo(self):
        tenacity.retry(
            lambda: subprocess.run(
                ["git", "clone", "--quiet", self.repo_url, self.git_repo_path],
                check=True,
            ),
            wait=tenacity.wait_fixed(30),
            stop=tenacity.stop_after_attempt(5),
        )()

        try:
            tenacity.retry(
                lambda: subprocess.run(
                    ["git", "pull", "--quiet", self.repo_url, "master"],
                    cwd=self.git_repo_path,
                    capture_output=True,
                    check=True,
                ),
                wait=tenacity.wait_fixed(30),
                stop=tenacity.stop_after_attempt(5),
            )()
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
