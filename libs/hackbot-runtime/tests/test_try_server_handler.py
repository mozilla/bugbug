"""Tests for the apply-side try-server action handler.

Mocks the Lando submission so these exercise the handler's own logic — loading
the agent-built patch series, generating the `try_task_config.json` commit,
result shaping. The generated commit is checked against real `git am`, since
"whatever `git format-patch` produces" is exactly what Lando's parser expects,
and a patch that `git am` rejects would fail server-side where it is much harder
to diagnose.
"""

import json
from base64 import b64decode
from datetime import datetime, timezone

import pytest
from hackbot_runtime.actions.handlers import ApplyContext, try_server_handler
from hackbot_runtime.actions.handlers.registry import get_handler
from hackbot_runtime.actions.try_server import TRY_ACTION_TYPES
from hackbot_runtime.changes import _git
from lando_client import LandoClient

_SUBMISSION = {
    "base_commit": "a" * 40,
    "base_commit_vcs": "git",
    "patch_format": "git-format-patch",
    "patches": ["cGF0Y2gtb25l"],  # b64("patch-one")
}


@pytest.fixture(autouse=True)
def _lando_env(monkeypatch):
    """Provide a dummy Lando token; the submission itself is always mocked."""
    monkeypatch.setenv("LANDO_ACCESS_TOKEN", "token")
    try_server_handler._client.cache_clear()
    yield
    try_server_handler._client.cache_clear()


def _ctx(submission=None, missing=False):
    async def download(key):
        assert key == "changes/try_push.json"
        if missing:
            raise FileNotFoundError(key)
        return json.dumps(submission or _SUBMISSION).encode()

    return ApplyContext(run_id="run-1", download_artifact=download)


@pytest.fixture
def submitted(monkeypatch):
    """Capture what the handler would send to Lando, returning job id 4321."""
    calls = []

    async def fake_submit(self, patches, base_commit, **kwargs):
        calls.append({"patches": patches, "base_commit": base_commit, **kwargs})
        return 4321

    monkeypatch.setattr(LandoClient, "submit_try_patches", fake_submit)
    return calls


def test_handler_is_registered():
    assert isinstance(get_handler("try_server.push"), try_server_handler.PushHandler)
    # Every type the recording side can emit is registered.
    assert all(get_handler(t) is not None for t in TRY_ACTION_TYPES)


# --- try_task_config ----------------------------------------------------- #


def test_try_task_config_selects_the_requested_tasks():
    config = try_server_handler.try_task_config(
        ["source-test-mozlint-eslint", "build-linux64/opt"]
    )

    assert config["version"] == 2
    parameters = config["parameters"]
    # Off, so the tasks asked for are the tasks that run.
    assert parameters["optimize_target_tasks"] is False
    assert parameters["try_task_config"]["tasks"] == [
        "build-linux64/opt",
        "source-test-mozlint-eslint",
    ]


def test_try_task_config_auto_matches_mach_try_auto():
    """Copied from TRY_AUTO_PARAMETERS; these name in-tree strategies verbatim."""
    parameters = try_server_handler.try_task_config(auto=True)["parameters"]

    assert parameters["try_mode"] == "try_auto"
    assert parameters["filters"] == ["try_auto"]
    assert parameters["test_manifest_loader"] == "bugbug"
    assert parameters["optimize_strategies"] == (
        "gecko_taskgraph.optimize:tryselect."
        "bugbug_reduced_manifests_config_selection_medium"
    )
    # auto lets CI choose, so optimisation is *on* and no labels are named.
    assert parameters["optimize_target_tasks"] is True
    assert parameters["try_task_config"] == {}
    # auto builds its config directly rather than via generate_try_task_config,
    # so unlike a label push it sets no TRY_SELECTOR.
    assert "env" not in parameters["try_task_config"]


def test_try_task_config_auto_calls_are_independent():
    """One push's narrowing must not bleed into the next one's config."""
    first = try_server_handler.try_task_config(
        auto=True, test_paths={"mochitest": ["dom/base/test"]}
    )
    second = try_server_handler.try_task_config(auto=True)

    assert "env" in first["parameters"]["try_task_config"]
    assert second["parameters"]["try_task_config"] == {}


@pytest.mark.parametrize(
    "kwargs",
    [
        {},  # neither a selection nor auto
        {"tasks": ["build-linux64/opt"], "auto": True},  # both at once
    ],
)
def test_try_task_config_demands_exactly_one_selection(kwargs):
    with pytest.raises(ValueError):
        try_server_handler.try_task_config(**kwargs)


@pytest.mark.parametrize("auto", [True, False])
def test_try_task_config_narrows_either_selection_to_test_paths(auto):
    """MOZHARNESS_TEST_PATHS is a modifier: it works with auto and with labels."""
    config = try_server_handler.try_task_config(
        None if auto else ["test-linux2404-64/opt-mochitest-1"],
        auto=auto,
        test_paths={"mochitest": ["dom/base/test", "dom/base/test"]},
    )

    env = config["parameters"]["try_task_config"]["env"]
    # mozharness looks the running suite up as a key, so the value has to stay a
    # JSON-encoded {suite: [paths]} mapping, deduplicated.
    assert json.loads(env["MOZHARNESS_TEST_PATHS"]) == {"mochitest": ["dom/base/test"]}


def test_try_task_config_patch_is_byte_identical_for_equivalent_requests():
    """Why everything is sorted: this config becomes a commit in the push.

    A re-applied action must rebuild the same commit, so the ordering the caller
    happened to use must not leak into the bytes.
    """
    stamp = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
    first = try_server_handler.try_task_config_patch(
        ["source-test-mozlint-eslint", "build-linux64/opt", "build-linux64/opt"],
        "Bug 1 - verify",
        stamp,
        test_paths={"xpcshell": ["dom/b", "dom/a"], "mochitest-plain": ["dom/c"]},
    )
    second = try_server_handler.try_task_config_patch(
        ["build-linux64/opt", "source-test-mozlint-eslint"],
        "Bug 1 - verify",
        stamp,
        test_paths={"mochitest-plain": ["dom/c"], "xpcshell": ["dom/a", "dom/b"]},
    )

    assert first == second


def test_try_task_config_deduplicates_tasks():
    config = try_server_handler.try_task_config(["build-linux64/opt"] * 3)

    assert config["parameters"]["try_task_config"]["tasks"] == ["build-linux64/opt"]


# --- try_task_config_patch ---------------------------------------------- #


def _apply_with_git_am(repo, patch: bytes):
    """`git am` the patch onto a fresh repo, returning the resulting commit log."""
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@test.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README").write_text("hello\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    patch_file = repo / "config.patch"
    patch_file.write_bytes(patch)
    _git(repo, "am", str(patch_file))
    return _git(repo, "log", "-1", "--format=%an <%ae>%n%B").strip()


def test_try_task_config_patch_applies_as_a_real_git_patch(tmp_path):
    patch = try_server_handler.try_task_config_patch(
        ["build-linux64/opt"], "Bug 1 - verify the fix"
    )

    log = _apply_with_git_am(tmp_path, patch)

    # Same identity `changes.py` stamps on the agent's own commits, so one push
    # does not show two different authors.
    assert log.startswith("Hackbot <hackbot@mozilla.tld>")
    assert "Bug 1 - verify the fix" in log
    written = json.loads((tmp_path / "try_task_config.json").read_text())
    assert written == try_server_handler.try_task_config(["build-linux64/opt"])


def test_try_task_config_patch_falls_back_to_a_default_title(tmp_path):
    patch = try_server_handler.try_task_config_patch(["build-linux64/opt"])

    assert "Subject: [PATCH] Hackbot try push" in patch.decode()
    assert (tmp_path / "try_task_config.json").exists() is False


def test_try_task_config_patch_collapses_a_multiline_title(tmp_path):
    """A newline in the title would end the Subject header early."""
    patch = try_server_handler.try_task_config_patch(
        ["build-linux64/opt"], "Bug 1 - a fix\nDate: bogus\n\nnot the body"
    )

    log = _apply_with_git_am(tmp_path, patch)

    assert log.splitlines()[1] == "Bug 1 - a fix Date: bogus not the body"


def test_try_task_config_patch_has_the_version_info_trailer():
    """Lando finds the end of the diff by scanning back for the `--` barrier."""
    lines = try_server_handler.try_task_config_patch(["build-linux64/opt"]).decode()

    assert lines.rstrip().splitlines()[-2] == "-- "


# --- PushHandler --------------------------------------------------------- #


async def test_apply_appends_the_config_commit_to_the_agents_patches(submitted):
    result = await try_server_handler.PushHandler().apply(
        {"tasks": ["build-linux64/opt"], "title": "Bug 1 - verify"}, _ctx()
    )

    assert result.status == "applied"
    call = submitted[0]
    assert call["base_commit"] == "a" * 40
    assert call["base_commit_vcs"] == "git"
    assert call["patch_format"] == "git-format-patch"
    assert call["repo_name"] == "try"
    # The agent's own commits come first, with the task selection as the tip.
    assert len(call["patches"]) == 2
    assert b64decode(call["patches"][0]) == b"patch-one"
    tip = b64decode(call["patches"][1]).decode()
    assert "try_task_config.json" in tip
    assert "build-linux64/opt" in tip


async def test_apply_returns_treeherder_and_lando_urls(submitted):
    result = await try_server_handler.PushHandler().apply(
        {"tasks": ["build-linux64/opt"]}, _ctx()
    )

    assert result.result["job_id"] == 4321
    assert result.result["tasks"] == ["build-linux64/opt"]
    # `url` is what a `{{actions.<ref>.url}}` placeholder resolves to, so it has
    # to be the one a human wants: the Treeherder view of the push.
    assert result.result["url"] == (
        "https://treeherder.mozilla.org/jobs?repo=try"
        "&landoInstance=lando-prod-2025&landoCommitID=4321"
    )
    assert result.result["lando_url"] == "https://lando.moz.tools/landings/4321"


async def test_apply_fails_without_a_patch_artifact(submitted):
    result = await try_server_handler.PushHandler().apply(
        {"tasks": ["build-linux64/opt"]}, _ctx(missing=True)
    )

    assert result.status == "failed"
    assert "No try push artifact" in result.error
    assert submitted == []


async def test_apply_fails_without_any_selection(submitted):
    result = await try_server_handler.PushHandler().apply({"tasks": []}, _ctx())

    assert result.status == "failed"
    assert submitted == []


async def test_apply_pushes_an_auto_selection_without_task_labels(submitted):
    result = await try_server_handler.PushHandler().apply(
        {"tasks": None, "auto": True}, _ctx()
    )

    assert result.status == "applied"
    assert result.result["selection"] == "auto"
    tip = b64decode(submitted[0]["patches"][-1]).decode()
    assert '"try_mode": "try_auto"' in tip


async def test_apply_carries_resolved_test_paths_into_the_config(submitted):
    result = await try_server_handler.PushHandler().apply(
        {"auto": True, "test_paths": {"mochitest": ["dom/base/test"]}}, _ctx()
    )

    assert result.result["test_paths"] == {"mochitest": ["dom/base/test"]}
    tip = b64decode(submitted[0]["patches"][-1]).decode()
    assert "MOZHARNESS_TEST_PATHS" in tip
    assert "dom/base/test" in tip


async def test_apply_reports_a_missing_lando_token(monkeypatch):
    """No token configured fails this one action, rather than the whole service."""
    monkeypatch.delenv("LANDO_ACCESS_TOKEN", raising=False)
    try_server_handler._client.cache_clear()

    result = await try_server_handler.PushHandler().apply(
        {"tasks": ["build-linux64/opt"]}, _ctx()
    )

    assert result.status == "failed"
    assert "access_token" in result.error


async def test_apply_reports_a_lando_rejection(monkeypatch):
    async def fake_submit(self, *args, **kwargs):
        raise RuntimeError("Lando returned HTTP 400: Repo try does not exist.")

    monkeypatch.setattr(LandoClient, "submit_try_patches", fake_submit)

    result = await try_server_handler.PushHandler().apply(
        {"tasks": ["build-linux64/opt"]}, _ctx()
    )

    assert result.status == "failed"
    assert "Repo try does not exist." in result.error
