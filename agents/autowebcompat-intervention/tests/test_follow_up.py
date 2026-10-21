"""Tests for the follow-up mode's inputs and the intervention prompts."""

import pytest
from agent_tools.firefox import FirefoxContext
from autowebcompat_tools.inputs import BugIdInput
from autowebcompat_tools.task import RunTracker, TaskConfig
from hackbot_agents.autowebcompat_intervention import agent
from hackbot_agents.autowebcompat_intervention.__main__ import AgentInputs
from hackbot_agents.autowebcompat_intervention.config import FOLLOW_UP_ACTIONS
from hackbot_agents.autowebcompat_intervention.result import InterventionPlanResult
from hackbot_runtime.actions.claude_sdk import actions_server_for
from pydantic import ValidationError

BROKER = "http://broker"


def test_follow_up_inputs_ok():
    inputs = AgentInputs(
        broker_url=BROKER, bug_id=1, revision_id=42, comment="@hackbot please fix"
    )
    assert inputs.revision_id == 42


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"bug_id": 1, "revision_id": 42}, "comment"),
        ({"bug_id": 1, "comment": "@hackbot please fix"}, "requires revision_id"),
        (
            {"bug_data": "report", "revision_id": 42, "comment": "@hackbot hi"},
            "bug_id",
        ),
    ],
)
def test_follow_up_inputs_must_be_coherent(fields, error):
    with pytest.raises(ValidationError, match=error):
        AgentInputs(broker_url=BROKER, **fields)


@pytest.fixture
def task_args(monkeypatch, tmp_path):
    """What an intervention task needs, minus the DevTools MCP binary."""
    monkeypatch.setattr(agent, "build_firefox_devtools_server", lambda **_: {})
    webcompat_dir = tmp_path / "browser/extensions/webcompat"
    webcompat_dir.mkdir(parents=True)
    (webcompat_dir / "AGENTS.md").write_text("WEBCOMPAT RULES")
    fx_ctx = FirefoxContext(
        source_dir=tmp_path,
        mozconfig=tmp_path / "mozconfig",
        objdir=tmp_path / "obj",
        binary=tmp_path / "obj/dist/bin/firefox",
        js_binary=tmp_path / "obj/dist/bin/js",
    )
    _, actions_server = actions_server_for(None, types=FOLLOW_UP_ACTIONS)
    server = {"type": "http", "url": BROKER}
    return {
        "source_repo": tmp_path,
        "fx_ctx": fx_ctx,
        "bugzilla_mcp_server": server,
        "phabricator_mcp_server": server,
        "phabricator_read_tools": [],
        "actions_server": actions_server,
        "action_tools": [],
    }


def test_follow_up_prompt_quotes_the_comments(task_args):
    comment = '<comment comment_id="7" type="regular">@hackbot use {css}</comment>'
    task = agent.InterventionFollowUp(
        TaskConfig(), RunTracker(), BugIdInput(bug_id=1), 42, comment, **task_args
    )

    prompt = task.user_prompt()

    assert f"<comments>\n{comment}\n</comments>" in prompt
    assert "D42" in prompt
    assert "WEBCOMPAT RULES" in prompt
    assert "--bugs 1" in prompt


def test_intervention_prompt_still_renders(task_args):
    plan = InterventionPlanResult(
        url="https://example.com/page", affected_platforms=["linux"], script_path=None
    )
    task = agent.Intervention(
        TaskConfig(), RunTracker(), BugIdInput(bug_id=1), plan, **task_args
    )

    prompt = task.user_prompt()

    assert "https://example.com/page" in prompt
    assert "WEBCOMPAT RULES" in prompt
