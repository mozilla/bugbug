"""Tests for ensure_source_repo (snapshot or shallow git checkout)."""

import subprocess
from pathlib import Path

import pytest
from hackbot_runtime import ensure_source_repo, source


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


def test_depth_reaches_older_push_ancestors(tmp_path):
    remote = tmp_path / "remote"
    _make_remote(remote)
    (remote / "README.md").write_text("second")
    _commit(remote, "second")
    (remote / "README.md").write_text("third")
    third = _commit(remote, "third")
    dest = tmp_path / "dest"
    # depth=3 fetches the whole 3-commit chain, so the root commit is present and
    # showable -- a depth=2 fetch (the default for a pinned ref) would not reach it.
    ensure_source_repo(dest, f"file://{remote}", ref=third, depth=3)
    count = subprocess.run(
        ["git", "-C", str(dest), "rev-list", "--count", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert int(count.stdout.strip()) == 3


def test_depth_applies_to_fresh_clone_without_ref(tmp_path):
    remote = tmp_path / "remote"
    _make_remote(remote)
    (remote / "README.md").write_text("second")
    _commit(remote, "second")
    (remote / "README.md").write_text("third")
    _commit(remote, "third")
    dest = tmp_path / "dest"
    ensure_source_repo(dest, f"file://{remote}", depth=3)
    count = subprocess.run(
        ["git", "-C", str(dest), "rev-list", "--count", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert int(count.stdout.strip()) == 3


GITHUB_URL = "https://github.com/org/repo.git"


def _redirect_github(monkeypatch, remote: Path) -> None:
    """Send git's requests for GITHUB_URL to the local ``remote``."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.file://{remote}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", GITHUB_URL)


def _snapshot(tmp_path: Path, remote: Path) -> bytes:
    """A .git tar of a full clone of ``remote``, as the daily snapshot Job makes.

    Its origin is a path that no longer exists, so a restore must fetch from the
    requested URL instead.
    """
    clone = tmp_path / "snapshot-clone"
    subprocess.run(
        ["git", "clone", "-q", "--no-checkout", f"file://{remote}", str(clone)],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(clone), "remote", "set-url", "origin", "/gone"], check=True
    )
    tar = subprocess.run(
        ["tar", "-c", "-C", str(clone), ".git"], check=True, capture_output=True
    )
    return tar.stdout


def test_fresh_checkout_uses_snapshot_and_fetches_newer_commits(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    _make_remote(remote)
    snapshot = _snapshot(tmp_path, remote)
    (remote / "README.md").write_text("newer")
    newer = _commit(remote, "after the snapshot")
    requested = []
    _redirect_github(monkeypatch, remote)
    monkeypatch.setenv("SOURCE_SNAPSHOT_BUCKET", "snapshots")
    monkeypatch.setattr(
        source,
        "_download",
        lambda bucket, name: requested.append((bucket, name)) or iter([snapshot]),
    )

    dest = tmp_path / "dest"
    ensure_source_repo(dest, GITHUB_URL, depth=1)

    assert requested == [("snapshots", "github/org/repo.tar")]
    rev = subprocess.run(
        ["git", "-C", str(dest), "rev-list", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert rev[0] == newer
    assert len(rev) == 2  # full history, not the requested depth
    assert (dest / "README.md").read_text() == "newer"


def test_failed_snapshot_restore_leaves_no_git_dir(tmp_path, monkeypatch):
    def fail(bucket, name):
        yield b"not a tar"
        raise OSError("connection reset")

    monkeypatch.setenv("SOURCE_SNAPSHOT_BUCKET", "snapshots")
    monkeypatch.setattr(source, "_download", fail)
    dest = tmp_path / "dest"

    assert not source._restore_snapshot(dest, GITHUB_URL, "HEAD")
    assert not dest.exists()


def test_failed_fetch_after_restore_falls_back_to_clone(tmp_path, monkeypatch):
    remote = tmp_path / "remote"
    _make_remote(remote)
    snapshot = _snapshot(tmp_path, remote)
    _redirect_github(monkeypatch, remote)
    monkeypatch.setenv("SOURCE_SNAPSHOT_BUCKET", "snapshots")
    monkeypatch.setattr(source, "_download", lambda bucket, name: iter([snapshot]))
    real_run = subprocess.run

    def fail_snapshot_fetch(cmd, **kwargs):
        if cmd[-2:] == ["origin", "HEAD"] and "--depth=1" not in cmd:
            raise subprocess.CalledProcessError(128, cmd)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(source.subprocess, "run", fail_snapshot_fetch)
    dest = tmp_path / "dest"
    ensure_source_repo(dest, GITHUB_URL)

    assert (dest / "README.md").read_text() == "hello"
    shallow = real_run(
        ["git", "-C", str(dest), "rev-parse", "--is-shallow-repository"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert shallow.stdout.strip() == "true"  # the fallback clone, not the snapshot


def test_snapshot_is_skipped_for_a_non_empty_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCE_SNAPSHOT_BUCKET", "snapshots")
    monkeypatch.setattr(source, "_download", lambda *a: pytest.fail("downloaded"))
    (tmp_path / "keep.txt").write_text("not ours")

    assert not source._restore_snapshot(tmp_path, GITHUB_URL, "HEAD")
    assert (tmp_path / "keep.txt").exists()


def test_snapshot_is_skipped_without_bucket_or_for_other_hosts(monkeypatch, tmp_path):
    monkeypatch.setattr(source, "_download", lambda *a: pytest.fail("downloaded"))
    monkeypatch.delenv("SOURCE_SNAPSHOT_BUCKET", raising=False)
    assert not source._restore_snapshot(tmp_path / "a", GITHUB_URL, "HEAD")
    monkeypatch.setenv("SOURCE_SNAPSHOT_BUCKET", "snapshots")
    assert not source._restore_snapshot(
        tmp_path / "b", "https://hg.mozilla.org/x", "HEAD"
    )


def test_snapshot_without_git_dir_leaves_an_enclosing_repo_alone(tmp_path, monkeypatch):
    outer = tmp_path / "outer"
    _make_remote(outer)
    subprocess.run(
        ["git", "-C", str(outer), "remote", "add", "origin", "/elsewhere"], check=True
    )
    (tmp_path / "junk").mkdir()
    (tmp_path / "junk" / "file").write_text("x")
    no_git = subprocess.run(
        ["tar", "-c", "-C", str(tmp_path / "junk"), "file"],
        check=True,
        capture_output=True,
    ).stdout
    monkeypatch.setenv("SOURCE_SNAPSHOT_BUCKET", "snapshots")
    monkeypatch.setattr(source, "_download", lambda bucket, name: iter([no_git]))

    assert not source._restore_snapshot(outer / "dest", GITHUB_URL, "HEAD")
    origin = subprocess.run(
        ["git", "-C", str(outer), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert origin.stdout.strip() == "/elsewhere"
    assert (outer / "README.md").read_text() == "hello"
