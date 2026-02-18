# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess
from pathlib import Path

from bugbug.tools.build_repair.config import WORKTREE_BASE_DIR


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
        subprocess.run(
            ["git", "worktree", "add", str(worktree_path), commit_hash],
            cwd=self.repo,
            check=True,
        )
        return worktree_path

    def cleanup(self, name: str) -> None:
        subprocess.run(
            ["git", "worktree", "remove", str(self.base_dir / name), "--force"],
            cwd=self.repo,
            check=True,
        )

    def cleanup_all(self) -> None:
        for entry in self.base_dir.iterdir():
            if entry.is_dir():
                subprocess.run(
                    ["git", "worktree", "remove", str(entry), "--force"],
                    cwd=self.repo,
                    check=False,
                )
