"""Tests for AgentInputs validation."""

import pytest
from hackbot_agents.bug_fix.__main__ import AgentInputs
from pydantic import ValidationError


def test_revision_requires_broker_url(monkeypatch):
    monkeypatch.delenv("PHABRICATOR_BROKER_URL", raising=False)
    with pytest.raises(ValidationError, match="phabricator_broker_url"):
        AgentInputs(bug_id=1, bugzilla_mcp_url="http://x", revision_id=42)


def test_revision_with_broker_url_ok():
    inputs = AgentInputs(
        bug_id=1,
        bugzilla_mcp_url="http://x",
        revision_id=42,
        phabricator_broker_url="http://broker",
    )
    assert inputs.phabricator_broker_url == "http://broker"


def test_no_revision_ok_without_broker_url(monkeypatch):
    monkeypatch.delenv("PHABRICATOR_BROKER_URL", raising=False)
    inputs = AgentInputs(bug_id=1, bugzilla_mcp_url="http://x")
    assert inputs.revision_id is None
