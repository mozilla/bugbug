"""Tests for AgentInputs validation."""

import pytest
from hackbot_agents.bug_fix.__main__ import AgentInputs
from hackbot_agents.bug_fix.agent import select_workflow
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
    _, prompt = select_workflow(
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
