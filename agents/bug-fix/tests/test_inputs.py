"""Tests for AgentInputs validation."""

import pytest
from hackbot_agents.bug_fix.__main__ import AgentInputs
from pydantic import ValidationError


def test_broker_url_required(monkeypatch):
    # Every run mounts the broker's Phabricator tools, follow-up or not.
    monkeypatch.delenv("PHABRICATOR_BROKER_URL", raising=False)
    with pytest.raises(ValidationError, match="phabricator_broker_url"):
        AgentInputs(bug_id=1, bugzilla_mcp_url="http://x")


def test_revision_requires_comment():
    with pytest.raises(ValidationError, match="comment"):
        AgentInputs(
            bug_id=1,
            bugzilla_mcp_url="http://x",
            revision_id=42,
            phabricator_broker_url="http://broker",
        )


def test_revision_with_broker_url_ok():
    inputs = AgentInputs(
        bug_id=1,
        bugzilla_mcp_url="http://x",
        revision_id=42,
        comment="@hackbot please fix",
        phabricator_broker_url="http://broker",
    )
    assert inputs.phabricator_broker_url == "http://broker"


def test_no_revision_ok_without_comment():
    inputs = AgentInputs(
        bug_id=1,
        bugzilla_mcp_url="http://x",
        phabricator_broker_url="http://broker",
    )
    assert inputs.revision_id is None
    assert inputs.comment is None


def test_phabricator_mcp_url_derived_from_broker_url():
    inputs = AgentInputs(
        bug_id=1,
        bugzilla_mcp_url="http://x",
        phabricator_broker_url="http://broker:8765/",
    )
    assert inputs.phabricator_mcp_url == "http://broker:8765/phabricator/mcp"
