"""Tests for the interactive elements a Slack message can carry.

The button's ``value`` is a contract with the listener in
``services/hackbot-api/app/slack/``, which the two packages cannot share a type
for, so what these pin is the wire shape: the keys that side declares, and
nothing it would refuse.
"""

import json

import pytest
from hackbot_runtime.slack_kit import create_start_agent_run_button
from slack_sdk.errors import SlackObjectFormationError
from slack_sdk.models.blocks import ConfirmObject


def _button(**overrides) -> dict:
    kwargs = {
        "agent_name": "bug-fix",
        "params": {"bug_id": 1968342},
        "dedupe_key": "frontend-triage-run:abc",
    }
    return create_start_agent_run_button(
        "Fix this bug", **{**kwargs, **overrides}
    ).to_dict()


def _value(**overrides) -> dict:
    return json.loads(_button(**overrides)["value"])


def test_the_button_is_a_block_kit_button_with_its_label():
    button = _button()
    assert button["type"] == "button"
    assert button["text"]["type"] == "plain_text"
    assert button["text"]["text"] == "Fix this bug"


def test_the_value_names_the_agent_the_inputs_and_the_key():
    assert _value() == {
        "agent_name": "bug-fix",
        "params": {"bug_id": 1968342},
        "dedupe_key": "frontend-triage-run:abc",
    }


def test_the_value_is_json_because_slack_carries_it_as_a_string():
    # Slack hands the value back verbatim, as a string, so the structure has to
    # survive the round trip encoded.
    assert isinstance(_button()["value"], str)


def test_the_key_is_required():
    # There is no un-rendering a button, so a caller has to name what the button is
    # one-shot about rather than leave it to be inferred.
    with pytest.raises(TypeError):
        create_start_agent_run_button("Go", agent_name="bug-fix", params={})


def test_a_run_to_apply_first_is_carried_only_when_there_is_one():
    assert "apply_run_id" not in _value()
    assert _value(apply_run_id="run-1")["apply_run_id"] == "run-1"


def test_the_action_id_names_the_agent_and_the_key():
    # Slack wants an `action_id` unique within its block, and the key is what
    # distinguishes two buttons that start the same agent. Derived rather than
    # passed in, so the id and the value cannot disagree about either.
    assert _button()["action_id"] == "start_agent_run:bug-fix:frontend-triage-run:abc"
    assert _button(agent_name="test-repair")["action_id"].startswith(
        "start_agent_run:test-repair:"
    )


def test_a_style_is_carried_only_when_asked_for():
    assert "style" not in _button()
    assert _button(style="primary")["style"] == "primary"


def test_a_confirmation_dialog_is_carried_only_when_asked_for():
    assert "confirm" not in _button()
    confirm = _button(
        confirm=ConfirmObject(title="Sure?", text="This writes to *Bugzilla*.")
    )["confirm"]
    assert confirm["title"]["text"] == "Sure?"
    # mrkdwn, so the dialog can name what it is about to write to.
    assert confirm["text"] == {"type": "mrkdwn", "text": "This writes to *Bugzilla*."}


def test_a_value_past_slacks_limit_is_refused_here():
    # Slack caps a button's `value` at 2000 characters. Built through the SDK's
    # models, that is caught where the button is made rather than by the API at
    # apply time, on a run that has already finished.
    with pytest.raises(SlackObjectFormationError):
        _button(params={"blob": "x" * 2100})


def test_a_label_past_slacks_limit_is_refused_here():
    with pytest.raises(SlackObjectFormationError):
        create_start_agent_run_button(
            "x" * 80,
            agent_name="bug-fix",
            params={},
            dedupe_key="k",
        ).to_dict()


def test_a_style_slack_does_not_accept_is_refused_here():
    with pytest.raises(SlackObjectFormationError):
        _button(style="chartreuse")
