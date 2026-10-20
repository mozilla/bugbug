"""Tests for recording a backout and building its revert patch."""

import subprocess

from hackbot_runtime.actions import lando
from hackbot_runtime.actions.recorder import ActionsRecorder
from hackbot_runtime.changes import _git, build_backout


def _repo_with_culprit(repo):
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "file.txt").write_text("a\nb\nc\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "file.txt").write_text("a\nbroken\nc\n")
    _git(repo, "commit", "-qam", "Bug 123 - Break b r=reviewer")
    culprit = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "other.txt").write_text("later\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Bug 456 - A later landing")
    return culprit


def test_backout_reverts_the_culprit_on_top_of_later_landings(tmp_path):
    culprit = _repo_with_culprit(tmp_path)
    head = _git(tmp_path, "rev-parse", "HEAD").strip()

    patch = build_backout(tmp_path, culprit[:12], "failures at test_b.js")

    assert _git(tmp_path, "rev-parse", "HEAD").strip() == head
    subprocess.run(["git", "-C", str(tmp_path), "am", "-q"], input=patch, check=True)
    assert (tmp_path / "file.txt").read_text() == "a\nb\nc\n"
    assert (tmp_path / "other.txt").exists()
    assert _git(tmp_path, "log", "-1", "--format=%B").strip() == (
        'Revert "Bug 123 - Break b r=reviewer" for causing failures at test_b.js.'
        f"\n\nThis reverts commit {culprit}."
    )


def test_record_backout_attaches_the_patch(tmp_path):
    culprit = _repo_with_culprit(tmp_path)
    artifacts = tmp_path / "artifacts"
    rec = ActionsRecorder(artifacts_dir=artifacts)

    action = lando.record_backout(
        rec,
        tmp_path,
        lando_repo="firefox-autoland",
        commit=culprit,
        reason="failures at test_b.js",
        reasoning="regression",
    )

    assert action["type"] == "lando.backout"
    assert action["params"] == {
        "lando_repo": "firefox-autoland",
        "commit": culprit,
        "reason": "failures at test_b.js",
    }
    [attachment] = action["attachments"]
    assert attachment["name"] == "backout.patch"
    assert (
        b"This reverts commit" in (artifacts / attachment["uploaded_key"]).read_bytes()
    )
