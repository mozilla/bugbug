"""Tests for the feedback invitation appended to comments at apply time.

See `with_feedback_link` in app/actions_applier.py. The link must appear only
for opted-in agents, only on comments, and only when the service is configured
to mint one — a broken or unverifiable URL in a permanent Bugzilla comment is
worse than no link at all.
"""

import uuid
from dataclasses import dataclass, field

import pytest
from app.actions_applier import with_feedback_link
from app.config import settings
from app.feedback_links import verify_token


@dataclass
class _FakeRun:
    agent: str = "frontend-triage"
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "feedback_token_secret", "test-feedback-secret")
    monkeypatch.setattr(settings, "feedback_public_base_url", "https://example.test")


def _comment(text="Root cause: the pref is never read."):
    return {"bug_id": 42, "text": text, "is_private": False}


def test_appends_an_invitation_carrying_a_verifiable_token():
    run = _FakeRun()
    out = with_feedback_link(run, "bugzilla.add_comment", _comment())

    assert "Was this analysis useful?" in out["text"]
    assert out["text"].startswith("Root cause: the pref is never read.")

    token = out["text"].split("/rate/")[1].split("?")[0]
    assert verify_token(token) == run.run_id
    assert "?v=up" in out["text"] and "?v=down" in out["text"]


def test_does_not_mutate_the_recorded_params():
    """A re-apply must re-derive the link, never stack a second copy."""
    params = _comment()
    out = with_feedback_link(_FakeRun(), "bugzilla.add_comment", params)

    assert "Was this analysis useful?" not in params["text"]
    assert out is not params
    assert params["bug_id"] == out["bug_id"]


def test_skipped_for_agents_that_have_not_opted_in():
    params = _comment()
    out = with_feedback_link(_FakeRun(agent="bug-fix"), "bugzilla.add_comment", params)
    assert out == params


def test_only_comments_get_a_link():
    """update_bug is the case that matters: it coalesces into the same PUT."""
    params = _comment()
    assert with_feedback_link(_FakeRun(), "bugzilla.update_bug", params) == params


@pytest.mark.parametrize(
    "setting", ["feedback_token_secret", "feedback_public_base_url"]
)
def test_skipped_when_unconfigured(monkeypatch, setting):
    monkeypatch.setattr(settings, setting, "")
    params = _comment()
    assert with_feedback_link(_FakeRun(), "bugzilla.add_comment", params) == params
