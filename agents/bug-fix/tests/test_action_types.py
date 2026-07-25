"""Tests for the per-flow enabled action lists (see bug_fix/config.py)."""

from hackbot_agents.bug_fix.config import (
    PHABRICATOR_FOLLOW_UP_ACTIONS,
    TRIAGE_AND_FIX_ACTIONS,
)
from hackbot_runtime.actions.phabricator import PATCH_ACTION_TYPES


def test_triage_flow_can_only_create_a_revision():
    assert "phabricator.submit_patch" in TRIAGE_AND_FIX_ACTIONS
    assert "phabricator.update_patch" not in TRIAGE_AND_FIX_ACTIONS
    # Nothing to comment on yet: there is no revision in this flow.
    assert "phabricator.add_comment" not in TRIAGE_AND_FIX_ACTIONS


def test_follow_up_flow_can_only_update_the_revision():
    assert "phabricator.update_patch" in PHABRICATOR_FOLLOW_UP_ACTIONS
    assert "phabricator.submit_patch" not in PHABRICATOR_FOLLOW_UP_ACTIONS
    assert "phabricator.add_comment" in PHABRICATOR_FOLLOW_UP_ACTIONS


def test_flows_never_offer_both_patch_actions():
    for types in (TRIAGE_AND_FIX_ACTIONS, PHABRICATOR_FOLLOW_UP_ACTIONS):
        assert len(PATCH_ACTION_TYPES.intersection(types)) == 1


def test_bugzilla_actions_are_available_in_both_flows():
    bugzilla_types = {t for t in TRIAGE_AND_FIX_ACTIONS if t.startswith("bugzilla.")}
    assert bugzilla_types
    assert bugzilla_types == {
        t for t in PHABRICATOR_FOLLOW_UP_ACTIONS if t.startswith("bugzilla.")
    }
