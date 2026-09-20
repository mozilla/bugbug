"""Tests for the Slack listener callbacks, called directly.

The shape Slack's own template uses: no HTTP, no signing, no Bolt app. Each
callback is a plain function, so the arguments Bolt would inject are passed in
by hand and what the callback does with them is checked. What a delivery has to
survive to *reach* one of these is covered in `test_slack_interactions.py`.
"""

import json
import logging
from unittest.mock import AsyncMock

import pytest
from app.slack.listeners.actions.start_agent_run import (
    StartAgentRunValue,
    start_agent_run_callback,
)
from hackbot_client import RunStatus, TriggeredRun
from pydantic import ValidationError

test_logger = logging.getLogger(__name__)

RUN_ID = "d3d5f21d-d716-4bb0-a812-8c9ef3e2f1c6"
DEDUPE_KEY = "frontend-triage-run:11111111-2222-3333-4444-555555555555"


def _action(
    agent_name: str = "bug-fix",
    params: dict | None = None,
    dedupe_key: str = DEDUPE_KEY,
) -> dict:
    """A clicked button, as Bolt hands it over."""
    return {
        "type": "button",
        "action_id": f"start_agent_run:{agent_name}",
        "value": json.dumps(
            {
                "agent_name": agent_name,
                "params": {"bug_id": 1234} if params is None else params,
                "dedupe_key": dedupe_key,
            }
        ),
    }


class TestStartAgentRun:
    def setup_method(self):
        self.fake_ack = AsyncMock()
        self.fake_client = AsyncMock()
        self.fake_client.trigger_run.return_value = TriggeredRun(
            run_id=RUN_ID, agent="bug-fix", status=RunStatus.pending, is_new=True
        )
        self.fake_respond = AsyncMock()
        self.context = {"hackbot_client": self.fake_client}
        self.body = {"user": {"id": "U0CLICKER"}}

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
        assert "already underway" in reply["text"]
        assert RUN_ID in reply["text"]
        assert reply["response_type"] == "ephemeral"
        # The channel keeps the button and its context; only the clicker is told.
        assert reply["replace_original"] is False

    async def test_a_collapsed_click_is_still_acknowledged(self):
        # It is a click Slack delivered and this app handled, whatever it caused.
        self.fake_client.trigger_run.return_value = self._triggered(is_new=False)

        await self._call()

        self.fake_ack.assert_awaited_once()

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
                "apply_run_id": "not-known-here",
            }
        )
        assert value.agent_name == "bug-fix"
