"""Tests for ensure_source_repo (shallow git checkout helper)."""

import subprocess
from pathlib import Path

from hackbot_runtime import ensure_source_repo


def _commit(path: Path, message: str) -> str:
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
            message,
        ],
        check=True,
    )
    rev = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return rev.stdout.strip()


def _make_remote(path: Path) -> str:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "README.md").write_text("hello")
    return _commit(path, "init")


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


def test_pins_to_ref_when_absent(tmp_path):
    remote = tmp_path / "remote"
    first = _make_remote(remote)
    # A second commit advances HEAD; pinning to `first` must ignore it.
    (remote / "README.md").write_text("world")
    _commit(remote, "second")
    dest = tmp_path / "dest"
    ensure_source_repo(dest, f"file://{remote}", ref=first)
    head = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert head.stdout.strip() == first
    assert (dest / "README.md").read_text() == "hello"


def test_pinned_ref_includes_parent_for_diff(tmp_path):
    remote = tmp_path / "remote"
    _make_remote(remote)
    (remote / "README.md").write_text("world")
    second = _commit(remote, "second")
    dest = tmp_path / "dest"
    ensure_source_repo(dest, f"file://{remote}", ref=second)
    # The parent must be present so the commit's own diff can be computed.
    show = subprocess.run(
        ["git", "-C", str(dest), "show", second],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "hello" in show.stdout
    assert "world" in show.stdout
