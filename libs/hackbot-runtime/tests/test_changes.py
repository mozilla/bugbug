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
    build_github_commits,
    build_phabricator_diff,
    build_try_push,
    has_changes,
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


# --- build_github_commits ------------------------------------------------ #

_REPO_URL = "https://github.com/owner/repo.git"


def _changes_by_path(commit):
    return {change["path"]: change for change in commit["changes"]}


def test_build_github_commits_one_entry_per_commit(tmp_path):
    base = _init_repo(tmp_path)
    _commit_change(tmp_path, "one\n", message="First\n\nWith a body.")
    _commit_change(tmp_path, "two\n", message="Second")

    payload = build_github_commits(tmp_path, base, _REPO_URL)

    assert payload["repo_url"] == _REPO_URL
    assert payload["base_commit"] == base
    first, second = payload["commits"]
    assert first["message"] == "First\n\nWith a body."
    assert second["message"] == "Second"
    assert first["author"]["name"] == "Test"
    assert first["author"]["email"] == "test@test.com"
    assert first["author"]["date"]
    # Each commit carries the file as it is at that commit, not at HEAD.
    assert base64.b64decode(first["changes"][0]["content"]) == b"one\n"
    assert base64.b64decode(second["changes"][0]["content"]) == b"two\n"
    assert second["changes"][0] == {
        "path": "file.txt",
        "mode": "100644",
        "content": base64.b64encode(b"two\n").decode(),
    }


def test_build_github_commits_includes_uncommitted_work(tmp_path):
    base = _init_repo(tmp_path)
    (tmp_path / "file.txt").write_text("edited\n")
    (tmp_path / "new.txt").write_text("untracked\n")

    payload = build_github_commits(tmp_path, base, _REPO_URL)

    (commit,) = payload["commits"]
    assert commit["message"] == "Uncommitted agent changes"
    assert set(_changes_by_path(commit)) == {"file.txt", "new.txt"}


def test_build_github_commits_records_deletions_modes_and_binaries(tmp_path):
    base = _init_repo(tmp_path)
    (tmp_path / "file.txt").unlink()
    script = tmp_path / "dir with space" / "run.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    (tmp_path / "image.bin").write_bytes(b"\x00\xff\x10")

    payload = build_github_commits(tmp_path, base, _REPO_URL)

    changes = _changes_by_path(payload["commits"][0])
    assert changes["file.txt"] == {
        "path": "file.txt",
        "mode": "100644",
        "deleted": True,
    }
    assert changes["dir with space/run.sh"]["mode"] == "100755"
    assert base64.b64decode(changes["image.bin"]["content"]) == b"\x00\xff\x10"


def test_build_github_commits_reports_a_rename_as_delete_and_add(tmp_path):
    base = _init_repo(tmp_path)
    _git(tmp_path, "mv", "file.txt", "renamed.txt")

    payload = build_github_commits(tmp_path, base, _REPO_URL)

    changes = _changes_by_path(payload["commits"][0])
    assert changes["file.txt"]["deleted"] is True
    assert base64.b64decode(changes["renamed.txt"]["content"]) == (
        b"line1\nline2\nline3\n"
    )


def test_build_github_commits_no_changes_returns_none(tmp_path):
    base = _init_repo(tmp_path)

    assert build_github_commits(tmp_path, base, _REPO_URL) is None


# --- has_changes ------------------------------------------------------- #


def test_has_changes_sees_an_uncommitted_edit(tmp_path):
    base = _init_repo(tmp_path)
    (tmp_path / "file.txt").write_text("line1\nline2 edited\nline3\n")
    assert has_changes(tmp_path, base) is True


def test_has_changes_sees_a_commit(tmp_path):
    base = _init_repo(tmp_path)
    _commit_change(tmp_path, "line1\nline2 committed\nline3\n")
    assert has_changes(tmp_path, base) is True


def test_has_changes_is_false_for_an_untouched_tree(tmp_path):
    base = _init_repo(tmp_path)
    assert has_changes(tmp_path, base) is False


def test_has_changes_leaves_the_tree_and_index_alone(tmp_path):
    base = _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("brand new\n")
    before = _git(tmp_path, "status", "--porcelain")

    assert has_changes(tmp_path, base) is True

    assert _git(tmp_path, "status", "--porcelain") == before
    assert _git(tmp_path, "rev-parse", "HEAD").strip() == base
