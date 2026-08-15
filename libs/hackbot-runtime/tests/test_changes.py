"""Tests for building submission payloads from a real git repo.

`collect()` (the pre-existing git-am patch collector) has no test coverage
either way and is out of scope here — this covers
`_synthetic_commit`/`build_phabricator_diff` and `build_try_push`, which run
against the agent's already-checked-out repo (see
hackbot_runtime.context.publish_changes).
"""

import base64
import builtins

from hackbot_runtime.changes import (
    _git,
    _synthetic_commit,
    build_phabricator_diff,
    build_try_push,
)


def _init_repo(repo, with_arcconfig=True):
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    if with_arcconfig:
        (repo / ".arcconfig").write_text(
            '{"phabricator.uri": "https://phabricator.services.mozilla.com/"}'
        )
    (repo / "file.txt").write_text("line1\nline2\nline3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base commit")
    return _git(repo, "rev-parse", "HEAD").strip()


def _commit_change(repo, content, message="the fix"):
    (repo / "file.txt").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


# --- _synthetic_commit ------------------------------------------------- #


def test_synthetic_commit_does_not_move_branch(tmp_path):
    base = _init_repo(tmp_path)
    head = _commit_change(tmp_path, "line1\nline2 modified\nline3\n")

    synthetic = _synthetic_commit(tmp_path, base)

    assert synthetic != head
    assert _git(tmp_path, "rev-parse", "HEAD").strip() == head


def test_synthetic_commit_parent_is_base(tmp_path):
    base = _init_repo(tmp_path)
    _commit_change(tmp_path, "line1\nline2 modified\nline3\n")

    synthetic = _synthetic_commit(tmp_path, base)

    parent = _git(tmp_path, "rev-parse", f"{synthetic}^").strip()
    assert parent == base


def test_synthetic_commit_tree_matches_head(tmp_path):
    base = _init_repo(tmp_path)
    _commit_change(tmp_path, "line1\nline2 modified\nline3\n")

    synthetic = _synthetic_commit(tmp_path, base)

    head_tree = _git(tmp_path, "rev-parse", "HEAD^{tree}").strip()
    synthetic_tree = _git(tmp_path, "rev-parse", f"{synthetic}^{{tree}}").strip()
    assert synthetic_tree == head_tree


def test_synthetic_commit_works_without_git_identity(tmp_path):
    base = _init_repo(tmp_path)
    _commit_change(tmp_path, "line1\nline2 modified\nline3\n")
    # Simulate a hardened container that refuses to invent an identity —
    # `commit-tree` would fail here if we didn't pass one explicitly.
    _git(tmp_path, "config", "user.useConfigOnly", "true")
    _git(tmp_path, "config", "--unset", "user.name")
    _git(tmp_path, "config", "--unset", "user.email")

    synthetic = _synthetic_commit(tmp_path, base)

    assert _git(tmp_path, "rev-parse", f"{synthetic}^").strip() == base


# --- build_phabricator_diff --------------------------------------------- #


def test_build_phabricator_diff_with_real_change(tmp_path):
    base = _init_repo(tmp_path)
    _commit_change(tmp_path, "line1\nline2 modified\nline3\n")

    result = build_phabricator_diff(tmp_path, base, "https://example.com/repo.git")

    assert result is not None
    payload = result["diff"]
    assert payload["sourceControlBaseRevision"] == base
    assert payload["sourceControlSystem"] == "git"
    assert payload["sourceMachine"] == "https://example.com/repo.git"
    assert len(payload["changes"]) == 1
    change = payload["changes"][0]
    assert change["currentPath"] == "file.txt"
    assert change["hunks"][0]["corpus"] == " line1\n-line2\n+line2 modified\n line3\n"

    # local:commits carries the git-side commit info moz-phab needs to rebuild
    # a commit; without it `moz-phab patch` fails with "a diff without commit
    # information". summary/message are filled in apply-side (they need the
    # revision URL), so they aren't present yet here.
    local_commits = result["local_commits"]
    assert len(local_commits) == 1
    node, entry = next(iter(local_commits.items()))
    assert entry["commit"] == node
    assert entry["parents"] == [base]
    assert entry["author"] == "Hackbot"
    assert entry["authorEmail"]  # populated from the synthesized commit
    assert entry["tree"]
    assert isinstance(entry["time"], int)
    assert "summary" not in entry
    assert "message" not in entry


def test_build_phabricator_diff_without_arcconfig_returns_none(tmp_path):
    base = _init_repo(tmp_path, with_arcconfig=False)
    _commit_change(tmp_path, "line1\nline2 modified\nline3\n")

    payload = build_phabricator_diff(tmp_path, base, "https://example.com/repo.git")

    assert payload is None


def test_build_phabricator_diff_no_changes_returns_none(tmp_path):
    base = _init_repo(tmp_path)
    # No commits made after base -- HEAD == base, nothing to squash/diff.

    payload = build_phabricator_diff(tmp_path, base, "https://example.com/repo.git")

    assert payload is None


def test_build_phabricator_diff_missing_mozphab_returns_none(tmp_path, monkeypatch):
    base = _init_repo(tmp_path)
    _commit_change(tmp_path, "line1\nline2 modified\nline3\n")

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("mozphab"):
            raise ImportError("mozphab not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    payload = build_phabricator_diff(tmp_path, base, "https://example.com/repo.git")

    assert payload is None


# --- build_try_push ------------------------------------------------------ #


def _decoded_patches(payload):
    return [base64.b64decode(patch).decode() for patch in payload["patches"]]


def test_build_try_push_one_patch_per_commit(tmp_path):
    base = _init_repo(tmp_path)
    _commit_change(tmp_path, "line1\nline2 modified\nline3\n", message="first fix")
    _commit_change(tmp_path, "line1\nline2 modified\nline3 too\n", message="second fix")

    payload = build_try_push(tmp_path, base)

    assert payload["base_commit"] == base
    assert payload["base_commit_vcs"] == "git"
    assert payload["patch_format"] == "git-format-patch"
    patches = _decoded_patches(payload)
    assert len(patches) == 2
    # Oldest first, and each patch is a standalone format-patch email (Lando
    # parses every array entry on its own).
    assert "Subject: [PATCH] first fix" in patches[0]
    assert "Subject: [PATCH] second fix" in patches[1]
    assert "second fix" not in patches[0]


def test_build_try_push_includes_uncommitted_work(tmp_path):
    base = _init_repo(tmp_path)
    (tmp_path / "file.txt").write_text("line1\nuncommitted\nline3\n")

    payload = build_try_push(tmp_path, base)

    assert len(payload["patches"]) == 1
    assert "+uncommitted" in _decoded_patches(payload)[0]


def test_build_try_push_no_changes_returns_none(tmp_path):
    base = _init_repo(tmp_path)

    assert build_try_push(tmp_path, base) is None


def test_build_try_push_rejects_abbreviated_base(tmp_path):
    base = _init_repo(tmp_path)
    _commit_change(tmp_path, "line1\nline2 modified\nline3\n")

    # Lando needs a full published hash; a short one would fail server-side with
    # a far less obvious error.
    assert build_try_push(tmp_path, base[:12]) is None
