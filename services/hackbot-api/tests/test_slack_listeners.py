"""Tests for the Slack listener callbacks, called directly.

The shape Slack's own template uses: no HTTP, no signing, no Bolt app. Each
callback is a plain function, so the arguments Bolt would inject are passed in
by hand and what the callback does with them is checked. What a delivery has to
survive to *reach* one of these is covered in `test_slack_interactions.py`.
"""

import json
import logging
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from app.slack.listeners.actions.start_agent_run import (
    StartAgentRunValue,
    start_agent_run_callback,
)
from hackbot_client import ApplyActionsResponse, RunAction, RunStatus, TriggeredRun
from pydantic import ValidationError

test_logger = logging.getLogger(__name__)

RUN_ID = "d3d5f21d-d716-4bb0-a812-8c9ef3e2f1c6"
DEDUPE_KEY = "frontend-triage-run:11111111-2222-3333-4444-555555555555"
TRIAGE_RUN_ID = "11111111-2222-3333-4444-555555555555"


def _applied(action_type: str, idx: int = 0) -> RunAction:
    return RunAction(idx=idx, type=action_type, status="applied")


def _failed(action_type: str, error: str, idx: int = 0) -> RunAction:
    return RunAction(idx=idx, type=action_type, status="failed", error=error)


def _actions(*actions: RunAction) -> ApplyActionsResponse:
    """The apply endpoint's answer, as the client hands it over."""
    return ApplyActionsResponse(list(actions))


def _action(
    agent_name: str = "bug-fix",
    params: dict | None = None,
    dedupe_key: str = DEDUPE_KEY,
    apply_run_id: str | None = None,
) -> dict:
    """A clicked button, as Bolt hands it over."""
    value = {
        "agent_name": agent_name,
        "params": {"bug_id": 1234} if params is None else params,
        "dedupe_key": dedupe_key,
    }
    if apply_run_id is not None:
        value["apply_run_id"] = apply_run_id
    return {
        "type": "button",
        "action_id": f"start_agent_run:{agent_name}",
        "value": json.dumps(value),
    }


class TestStartAgentRun:
    def setup_method(self):
        self.fake_ack = AsyncMock()
        self.fake_client = AsyncMock()
        self.fake_client.trigger_run.return_value = TriggeredRun(
            run_id=RUN_ID, agent="bug-fix", status=RunStatus.pending, is_new=True
        )
        self.fake_respond = AsyncMock()
        # The apply endpoint answers with every action of the run and its state
        # after the pass; by default here, all of them landed.
        self.fake_client.apply_actions.return_value = _actions(
            _applied("bugzilla.add_comment")
        )
        self.context = {"hackbot_client": self.fake_client}
        self.body = {"user": {"id": "U0CLICKER"}}

    def _record_order(self) -> list[str]:
        """Every call the callback makes, in the order it makes them."""
        order: list[str] = []
        self.fake_client.apply_actions.side_effect = lambda *_, **__: (
            order.append("apply") or _actions(_applied("bugzilla.add_comment"))
        )
        self.fake_client.trigger_run.side_effect = lambda *_, **__: (
            order.append("trigger") or self._triggered(is_new=True)
        )
        return order

    def _triggered(self, *, is_new: bool, status=RunStatus.pending):
        return TriggeredRun(
            run_id=RUN_ID, agent="bug-fix", status=status, is_new=is_new
        )

    async def _call(self, action: dict | None = None):
        await start_agent_run_callback(
            ack=self.fake_ack,
            action=_action() if action is None else action,
            body=self.body,
            context=self.context,
            respond=self.fake_respond,
            logger=test_logger,
        )

    async def test_starts_the_run_the_button_asks_for(self):
        await self._call()

        self.fake_client.trigger_run.assert_awaited_once_with(
            "bug-fix", {"bug_id": 1234}, dedupe_key=DEDUPE_KEY
        )

    async def test_the_agent_and_inputs_come_from_the_buttons_value(self):
        await self._call(_action(agent_name="test-repair", params={"task_id": "abc"}))

        self.fake_client.trigger_run.assert_awaited_once_with(
            "test-repair", {"task_id": "abc"}, dedupe_key=DEDUPE_KEY
        )

    async def test_the_key_is_forwarded_so_the_api_can_collapse_repeats(self):
        # The whole of #6732: the receiver keeps no state, so this key reaching
        # the create call is what makes a second click land on the first run.
        await self._call(_action(dedupe_key="frontend-triage-run:abc"))

        assert self.fake_client.trigger_run.await_args.kwargs["dedupe_key"] == (
            "frontend-triage-run:abc"
        )

    async def test_the_click_is_acknowledged(self):
        await self._call()

        self.fake_ack.assert_awaited_once()

    async def test_the_run_is_started_before_the_click_is_acknowledged(self):
        # Not a style preference: `ack()` releases the response, and the run has
        # to have been asked for by then. See the FIXME on the callback.
        order = []
        self.fake_ack.side_effect = lambda *_: order.append("ack")
        self.fake_client.trigger_run.side_effect = lambda *_, **__: (
            order.append("trigger")
            or TriggeredRun(
                run_id=RUN_ID, agent="bug-fix", status=RunStatus.pending, is_new=True
            )
        )

        await self._call()

        assert order == ["trigger", "ack"]

    async def test_a_new_run_tells_the_clicker_nothing(self):
        # The run started and the message they clicked is the record of it.
        await self._call()

        self.fake_respond.assert_not_awaited()

    async def test_a_click_that_started_nothing_says_where_the_run_is(self):
        self.fake_client.trigger_run.return_value = self._triggered(
            is_new=False, status=RunStatus.running
        )

        await self._call()

        self.fake_respond.assert_awaited_once()
        reply = self.fake_respond.await_args.kwargs
        assert "already triggered" in reply["text"]
        assert RUN_ID in reply["text"]
        assert reply["response_type"] == "ephemeral"
        # The channel keeps the button and its context; only the clicker is told.
        assert reply["replace_original"] is False

    async def test_a_collapsed_click_is_still_acknowledged(self):
        # It is a click Slack delivered and this app handled, whatever it caused.
        self.fake_client.trigger_run.return_value = self._triggered(is_new=False)

        await self._call()

        self.fake_ack.assert_awaited_once()

    async def test_a_button_with_nothing_to_apply_only_triggers(self):
        await self._call()

        self.fake_client.apply_actions.assert_not_awaited()

    async def test_a_held_result_is_applied_before_the_run_starts(self):
        # The ordering is the point: the agent about to read the bug has to find
        # the analysis already on it.
        order = self._record_order()

        await self._call(_action(apply_run_id=TRIAGE_RUN_ID))

        assert order == ["apply", "trigger"]

    async def test_the_run_named_by_the_button_is_the_one_applied(self):
        await self._call(_action(apply_run_id=TRIAGE_RUN_ID))

        self.fake_client.apply_actions.assert_awaited_once_with(UUID(TRIAGE_RUN_ID))

    async def test_an_action_that_did_not_land_starts_nothing(self):
        # The apply endpoint answers `200` for a pass that ran, not one that
        # worked: a handler that raised is a `failed` row, not an error. Missing
        # that would start the run against a bug with no analysis on it.
        self.fake_client.apply_actions.return_value = _actions(
            _applied("bugzilla.add_comment"),
            _failed("bugzilla.add_comment", "Bugzilla rejected the comment", idx=1),
        )

        await self._call(_action(apply_run_id=TRIAGE_RUN_ID))

        self.fake_client.trigger_run.assert_not_awaited()
        # Answered, not crashed: the clicker is told why, and the click is
        # acknowledged because this app handled it.
        self.fake_respond.assert_awaited_once()
        self.fake_ack.assert_awaited_once()

    async def test_the_clicker_is_told_no_run_was_started(self):
        self.fake_client.apply_actions.return_value = _actions(
            _failed("bugzilla.add_comment", "Bugzilla rejected the comment")
        )

        await self._call(_action(apply_run_id=TRIAGE_RUN_ID))

        told = self.fake_respond.await_args.kwargs["text"]
        assert "No new agent run was started" in told
        # The handler's own error stays in the log: the clicker needs to know
        # the click did nothing, not what Bugzilla said back.
        assert "Bugzilla rejected the comment" not in told

    async def test_the_failure_is_logged_as_an_error(self, caplog):
        # The clicker gets a sentence; whoever is on call needs the rest.
        self.fake_client.apply_actions.return_value = _actions(
            _failed("bugzilla.add_comment", "Bugzilla rejected the comment")
        )

        with caplog.at_level(logging.ERROR):
            await self._call(_action(apply_run_id=TRIAGE_RUN_ID))

        assert "Bugzilla rejected the comment" in caplog.text
        assert TRIAGE_RUN_ID in caplog.text

    async def test_an_apply_where_everything_landed_starts_the_run(self):
        self.fake_client.apply_actions.return_value = _actions(
            _applied("bugzilla.add_comment"),
            _applied("slack.post_message", idx=1),
        )

        await self._call(_action(apply_run_id=TRIAGE_RUN_ID))

        self.fake_client.trigger_run.assert_awaited_once()

    async def test_a_failing_apply_starts_nothing(self):
        # Starting anyway would hand the agent a bug without the analysis this
        # press was approving.
        self.fake_client.apply_actions.side_effect = RuntimeError("Bugzilla said no")

        with pytest.raises(RuntimeError):
            await self._call(_action(apply_run_id=TRIAGE_RUN_ID))

        self.fake_client.trigger_run.assert_not_awaited()
        self.fake_ack.assert_not_awaited()

    async def test_a_value_naming_something_that_is_not_a_run_is_refused(self):
        with pytest.raises(ValidationError):
            await self._call(_action(apply_run_id="not-a-uuid"))

        self.fake_client.apply_actions.assert_not_awaited()
        self.fake_client.trigger_run.assert_not_awaited()

    async def test_a_failing_trigger_is_not_swallowed(self):
        # The click should surface as a `500`, not be logged and forgotten: a
        # button that did nothing is worse than one that visibly failed.
        self.fake_client.trigger_run.side_effect = RuntimeError("Jobs is unhappy")

        with pytest.raises(RuntimeError):
            await self._call()

        self.fake_ack.assert_not_awaited()

    async def test_a_value_missing_the_agent_is_refused_before_anything_starts(self):
        action = _action()
        action["value"] = json.dumps(
            {"params": {"bug_id": 1234}, "dedupe_key": DEDUPE_KEY}
        )

        with pytest.raises(ValidationError):
            await self._call(action)

        self.fake_client.trigger_run.assert_not_awaited()

    async def test_a_button_without_a_key_starts_nothing(self):
        # A button that shipped without one would otherwise run per click, which
        # is the failure the key exists to prevent.
        action = _action()
        action["value"] = json.dumps(
            {"agent_name": "bug-fix", "params": {"bug_id": 1234}}
        )

        with pytest.raises(ValidationError):
            await self._call(action)

        self.fake_client.trigger_run.assert_not_awaited()

    async def test_a_button_with_a_blank_key_starts_nothing(self):
        with pytest.raises(ValidationError):
            await self._call(_action(dedupe_key=""))

        self.fake_client.trigger_run.assert_not_awaited()

    async def test_a_value_that_is_not_an_object_is_refused(self):
        action = _action()
        action["value"] = json.dumps("bug-fix")

        with pytest.raises(ValidationError):
            await self._call(action)

        self.fake_client.trigger_run.assert_not_awaited()


class TestStartAgentRunValue:
    def test_params_default_to_empty(self):
        value = StartAgentRunValue.model_validate(
            {"agent_name": "bug-fix", "dedupe_key": DEDUPE_KEY}
        )
        assert value.params == {}

    def test_a_run_to_apply_first_is_optional(self):
        value = StartAgentRunValue.model_validate(
            {"agent_name": "bug-fix", "dedupe_key": DEDUPE_KEY}
        )
        assert value.apply_run_id is None

    def test_a_run_to_apply_first_is_parsed_as_a_run_id(self):
        value = StartAgentRunValue.model_validate(
            {
                "agent_name": "bug-fix",
                "dedupe_key": DEDUPE_KEY,
                "apply_run_id": TRIAGE_RUN_ID,
            }
        )
        assert value.apply_run_id == UUID(TRIAGE_RUN_ID)

    def test_the_key_is_required(self):
        with pytest.raises(ValidationError):
            StartAgentRunValue.model_validate({"agent_name": "bug-fix"})

    def test_unknown_keys_are_ignored_not_rejected(self):
        # The two sides deploy separately: the buttons are drawn in an agent
        # image, the value is read here. An agent that starts sending a field
        # before this service knows it should lose the field, not every click.
        value = StartAgentRunValue.model_validate(
            {
                "agent_name": "bug-fix",
                "dedupe_key": DEDUPE_KEY,
                "future_field": "not-known-here",
            }
        )
        assert value.agent_name == "bug-fix"
