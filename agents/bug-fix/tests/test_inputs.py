"""Tests for AgentInputs validation."""

from types import SimpleNamespace

import hackbot_agents.bug_fix.__main__ as bug_fix_main
import pytest
from hackbot_agents.bug_fix.__main__ import AgentInputs, check_pull_request_repo
from hackbot_agents.bug_fix.agent import PullRequest, select_workflow
from pydantic import ValidationError


def test_broker_url_required(monkeypatch):
    # Every run mounts the broker's Phabricator tools, follow-up or not.
    monkeypatch.delenv("BROKER_URL", raising=False)
    with pytest.raises(ValidationError, match="broker_url"):
        AgentInputs(bug_id=1)


def test_revision_requires_comment():
    with pytest.raises(ValidationError, match="comment"):
        AgentInputs(
            bug_id=1,
            broker_url="http://broker",
            revision_id=42,
        )


def test_revision_with_broker_url_ok():
    inputs = AgentInputs(
        bug_id=1,
        broker_url="http://broker",
        revision_id=42,
        comment="@hackbot please fix",
    )
    assert inputs.broker_url == "http://broker"


def test_no_revision_ok_without_comment():
    inputs = AgentInputs(
        bug_id=1,
        broker_url="http://broker",
    )
    assert inputs.revision_id is None
    assert inputs.comment is None


def test_bugzilla_needinfo_rejects_phabricator_context():
    with pytest.raises(ValidationError, match="cannot be combined"):
        AgentInputs(
            bug_id=1,
            broker_url="http://broker",
            revision_id=42,
            comment="@hackbot please fix",
            bugzilla_needinfo_flag_id=123,
        )


def test_bugzilla_needinfo_requires_comment_context():
    with pytest.raises(ValidationError, match="comment"):
        AgentInputs(
            bug_id=1,
            broker_url="http://broker",
            bugzilla_needinfo_flag_id=123,
        )


def test_bugzilla_needinfo_comment_context_is_in_prompt(tmp_path):
    context = (
        "Check whether Bugzilla user user@example.com posted a comment at exactly "
        "2026-08-21T16:36:29."
    )
    prompt = select_workflow(
        bug=1,
        revision_id=None,
        comment=context,
        bugzilla_needinfo_flag_id=123,
        rules_dir=tmp_path,
    )

    assert context in prompt


def test_mcp_urls_derived_from_broker_url():
    inputs = AgentInputs(
        bug_id=1,
        broker_url="http://broker:8765/",
    )
    assert inputs.bugzilla_mcp_url == "http://broker:8765/bugzilla/mcp"
    assert inputs.phabricator_mcp_url == "http://broker:8765/phabricator/mcp"


HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"
SOURCE_URL = "https://github.com/mozilla-firefox/firefox.git"


def _pr_inputs(**fields):
    return AgentInputs(
        **{
            "broker_url": "http://broker",
            "github_repo": "mozilla-firefox/firefox",
            "github_pr_number": 42,
            "comment": "@hackbot please fix",
            **fields,
        }
    )


def test_github_pr_needs_no_bug():
    inputs = _pr_inputs(github_head_sha=HEAD_SHA, github_base_ref="main")
    assert inputs.bug_id is None
    assert inputs.github_head_sha == HEAD_SHA
    assert inputs.github_base_ref == "main"


def test_bug_required_without_github_pr():
    with pytest.raises(ValidationError, match="bug_id"):
        AgentInputs(broker_url="http://broker")


def test_github_pr_requires_comment():
    with pytest.raises(ValidationError, match="comment"):
        _pr_inputs(comment=None)


@pytest.mark.parametrize(
    "fields",
    [{"github_repo": None}, {"github_pr_number": None}],
)
def test_github_repo_and_pr_number_go_together(fields):
    with pytest.raises(ValidationError, match="must be set together"):
        _pr_inputs(bug_id=1, **fields)


@pytest.mark.parametrize(
    "fields", [{"revision_id": 7}, {"bugzilla_needinfo_flag_id": 7}]
)
def test_github_pr_excludes_other_follow_ups(fields):
    with pytest.raises(ValidationError, match="cannot be combined"):
        _pr_inputs(**fields)


def test_github_pins_require_a_pr():
    with pytest.raises(ValidationError, match="require github_pr_number"):
        AgentInputs(bug_id=1, broker_url="http://broker", github_head_sha=HEAD_SHA)


@pytest.mark.parametrize(
    "fields",
    [
        {"github_head_sha": "0123456"},
        {"github_base_ref": "--upload-pack=evil"},
        {"github_repo": "firefox"},
    ],
)
def test_github_fields_are_validated(fields):
    with pytest.raises(ValidationError):
        _pr_inputs(**fields)


@pytest.mark.parametrize(
    "github_repo", ["mozilla-firefox/firefox", "Mozilla-Firefox/Firefox"]
)
def test_pull_request_from_the_source_repo_is_accepted(github_repo):
    check_pull_request_repo(github_repo, SOURCE_URL)


@pytest.mark.parametrize(
    "source_url",
    [
        SOURCE_URL,
        "https://gitlab.com/mozilla-firefox/firefox.git",
        "git@github.com:mozilla-firefox/firefox.git",
    ],
)
def test_pull_request_from_another_repo_is_refused(source_url):
    with pytest.raises(RuntimeError, match="not this agent's source"):
        check_pull_request_repo("mozilla/gecko-dev", source_url)


def test_github_pr_prompt_quotes_the_request_and_merge_base(tmp_path):
    prompt = select_workflow(
        bug=None,
        revision_id=None,
        comment="<comment>please fix</comment>",
        bugzilla_needinfo_flag_id=None,
        rules_dir=tmp_path,
        pull_request=PullRequest("mozilla-firefox/firefox", 42, "abc123"),
    )

    assert "pull request mozilla-firefox/firefox#42, which" in prompt
    assert "<comment>please fix</comment>" in prompt
    assert "git diff abc123 HEAD" in prompt
    assert "Phabricator revision D" not in prompt


def test_github_pr_prompt_names_its_bug(tmp_path):
    prompt = select_workflow(
        bug=7,
        revision_id=None,
        comment="please fix",
        bugzilla_needinfo_flag_id=None,
        rules_dir=tmp_path,
        pull_request=PullRequest("mozilla-firefox/firefox", 42, "abc123"),
    )

    assert "mozilla-firefox/firefox#42 (bug 7)" in prompt


@pytest.mark.parametrize(
    ("base_env", "expected_base"), [(None, "HEAD"), ("release", "release")]
)
async def test_main_checks_out_the_pull_request(monkeypatch, base_env, expected_base):
    for name in ("BUG_ID", "REVISION_ID", "BUGZILLA_NEEDINFO_FLAG_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BROKER_URL", "http://broker")
    monkeypatch.setenv("GITHUB_REPO", "mozilla-firefox/firefox")
    monkeypatch.setenv("GITHUB_PR_NUMBER", "42")
    monkeypatch.setenv("GITHUB_HEAD_SHA", HEAD_SHA)
    monkeypatch.setenv("COMMENT", "please fix")
    if base_env is None:
        monkeypatch.delenv("GITHUB_BASE_REF", raising=False)
    else:
        monkeypatch.setenv("GITHUB_BASE_REF", base_env)

    checkouts = []

    async def fake_checkout(ctx, number, base, head_sha=None):
        checkouts.append((number, base, head_sha))
        return "abc123"

    async def fake_run(**kwargs):
        return kwargs

    monkeypatch.setattr(bug_fix_main, "checkout_pull_request", fake_checkout)
    monkeypatch.setattr(bug_fix_main, "run_bug_fix", fake_run)
    ctx = SimpleNamespace(
        config=SimpleNamespace(source=SimpleNamespace(repo_url=SOURCE_URL)),
        repo_path="/workspace/firefox",
        firefox=None,
        log_path=None,
        actions=None,
    )

    kwargs = await bug_fix_main.main(ctx)

    assert checkouts == [(42, expected_base, HEAD_SHA)]
    assert kwargs["bug"] is None
    assert kwargs["pull_request"] == PullRequest(
        "mozilla-firefox/firefox", 42, "abc123"
    )
