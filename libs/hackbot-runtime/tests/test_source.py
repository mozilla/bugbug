"""Tests for ensure_source_repo (shallow git checkout helper)."""

import subprocess
from pathlib import Path

from hackbot_runtime import ensure_source_repo


def _make_remote(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("hello")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=test",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        check=True,
    )


def test_clones_when_absent(tmp_path):
    remote = tmp_path / "remote"
    _make_remote(remote)
    dest = tmp_path / "dest"
    ensure_source_repo(dest, f"file://{remote}")
    assert (dest / ".git").is_dir()
    assert (dest / "README.md").read_text() == "hello"


def test_idempotent_update_when_present(tmp_path):
    remote = tmp_path / "remote"
    _make_remote(remote)
    dest = tmp_path / "dest"
    ensure_source_repo(dest, f"file://{remote}")
    # Second call takes the fetch + hard-reset branch and must still succeed.
    ensure_source_repo(dest, f"file://{remote}")
    assert (dest / "README.md").read_text() == "hello"
