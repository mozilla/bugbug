"""Tests for building the Phabricator diff payload from a real git repo.

`collect()` (the pre-existing git-am patch collector) has no test coverage
either way and is out of scope here — this covers the new
`_synthetic_commit`/`build_phabricator_diff`, which run against the agent's
already-checked-out repo (see hackbot_runtime.context.publish_changes).
"""

import builtins

from hackbot_runtime.changes import _git, _synthetic_commit, build_phabricator_diff


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

    payload = build_phabricator_diff(tmp_path, base, "https://example.com/repo.git")

    assert payload is not None
    assert payload["sourceControlBaseRevision"] == base
    assert payload["sourceControlSystem"] == "git"
    assert payload["sourceMachine"] == "https://example.com/repo.git"
    assert len(payload["changes"]) == 1
    change = payload["changes"][0]
    assert change["currentPath"] == "file.txt"
    assert change["hunks"][0]["corpus"] == " line1\n-line2\n+line2 modified\n line3\n"


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
