# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess
from logging import getLogger
from pathlib import Path

from bugbug.tools.build_repair.config import WORKTREE_BASE_DIR

logger = getLogger(__name__)


class WorktreeManager:
    """Manages git worktrees for parallel evaluation runs against a Firefox repo."""

    def __init__(
        self,
        firefox_repo_path: str | Path,
        base_dir: str = WORKTREE_BASE_DIR,
    ):
        self.repo = Path(firefox_repo_path)
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create(self, commit_hash: str, name: str) -> Path:
        worktree_path = self.base_dir / name
        logger.info(
            "Creating worktree %s at %s (commit=%s)", name, worktree_path, commit_hash
        )
        if worktree_path.exists():
            self.cleanup(name)
        # --force twice to operate on locked worktrees (see https://git-scm.com/docs/git-worktree#_options)
        subprocess.run(
            [
                "git",
                "worktree",
                "add",
                "--force",
                "--force",
                str(worktree_path),
                commit_hash,
            ],
            cwd=self.repo,
            check=True,
        )
        logger.info("Worktree %s created", name)
        return worktree_path

    def cleanup(self, name: str) -> None:
        logger.info("Cleaning up worktree %s", name)
        # --force twice to operate on locked worktrees (see https://git-scm.com/docs/git-worktree#_options)
        result = subprocess.run(
            [
                "git",
                "worktree",
                "remove",
                "--force",
                "--force",
                str(self.base_dir / name),
            ],
            cwd=self.repo,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(
                "Failed to remove worktree %s: %s", name, result.stderr.strip()
            )
        else:
            logger.info("Worktree %s removed", name)

    def cleanup_all(self) -> None:
        logger.info("Cleaning up all worktrees in %s", self.base_dir)
        for entry in self.base_dir.iterdir():
            if entry.is_dir():
                logger.info("Removing worktree %s", entry)
                subprocess.run(
                    ["git", "worktree", "remove", "--force", "--force", str(entry)],
                    cwd=self.repo,
                    check=False,
                )
