"""Shared fixtures for the uplift agent tests."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_git_config(monkeypatch) -> None:
    """Keep the developer's own git config out of the repositories these build.

    Set in the environment rather than passed per call, because the runtime
    makes git calls of its own: with global commit signing or a hooks path
    configured, those would fail too. Identity still comes from each
    repository's local config.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")


@pytest.fixture
def git_in() -> Callable[..., str]:
    """Run git in a repository and return its stripped stdout.

    ``check=False`` is for commands expected to fail, like a cherry-pick that
    conflicts on purpose.
    """

    def run(repo: Path, *args: str, check: bool = True) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=check,
            capture_output=True,
            text=True,
        ).stdout.strip()

    return run


@pytest.fixture
def repo(tmp_path, git_in) -> Path:
    """A git repository at one commit, standing in for the prepared checkout.

    Real rather than stubbed: what the run checks afterwards are facts about
    git's own state, which a stub could only assert we asked for.
    """
    path = tmp_path / "firefox"
    path.mkdir()
    git_in(path, "init", "-q")
    git_in(path, "config", "user.email", "agent@example.com")
    git_in(path, "config", "user.name", "Agent")
    (path / "f.txt").write_text("line1\nline2\nline3\n")
    git_in(path, "add", "-A")
    git_in(path, "commit", "-qm", "base")
    return path


class RecordingPublisher:
    """A ``publish_file`` callable that records calls instead of uploading.

    Bodies are read when the call is made, as a real uploader reads them, so a
    later write to the same path cannot change what a test sees published.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, Path, str | None]] = []
        self.bodies: dict[str, str] = {}

    def __call__(self, name: str, path: Path, content_type: str | None) -> str:
        self.calls.append((name, Path(path), content_type))
        self.bodies[name] = Path(path).read_text()
        return f"published://{name}"

    @property
    def names(self) -> list[str]:
        return [name for name, _, _ in self.calls]


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()
