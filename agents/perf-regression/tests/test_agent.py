# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import asyncio
from unittest.mock import MagicMock

import pytest
from claude_agent_sdk import CLIConnectionError, CLIJSONDecodeError, ResultMessage
from hackbot_agents.perf_regression import agent
from hackbot_runtime import AgentError


@pytest.mark.parametrize(
    "mode", ["immediate", "recover", "exhausted", "sdk_error", "late_error", "timeout"]
)
async def test_candidate_session_recovery(monkeypatch, tmp_path, mode):
    context = None
    queries = []
    options = []

    def build_sdk_server(name, ctx, tools):
        nonlocal context
        context = ctx

    class Client:
        def __init__(self, **kwargs):
            options.append(kwargs["options"])

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def query(self, prompt):
            queries.append(prompt)

        async def receive_response(self):
            if mode == "timeout" and len(queries) > 1:
                await asyncio.sleep(10)
            if mode in {"immediate", "late_error"} or (
                mode == "recover" and len(queries) == 2
            ):
                await agent.submit_result(
                    context,
                    dict(
                        has_candidate=True,
                        summary="Candidate",
                        regression_location="layout/grid.cpp: MeasureGrid on the main thread",
                        root_cause="Cause",
                        profile_evidence="Evidence",
                        correctness_checks=["lint passed"],
                    ),
                )
            yield ResultMessage(
                subtype="error_max_turns"
                if mode in {"sdk_error", "late_error"}
                else "success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=mode in {"sdk_error", "late_error"},
                num_turns=2,
                session_id="same-session",
                total_cost_usd=0.25 * (len(queries) + 1),
            )

    monkeypatch.setattr(agent, "build_sdk_server", build_sdk_server)
    monkeypatch.setattr(agent, "ClaudeSDKClient", Client)
    monkeypatch.setattr(agent, "Reporter", MagicMock())
    monkeypatch.setattr(agent, "RECOVERY_TIMEOUT_SECONDS", 0.01)
    args = dict(
        repo=tmp_path,
        evidence=tmp_path,
        previous=[],
        model=None,
        max_turns=500,
        effort="high",
        log=tmp_path / "log",
    )
    if mode in {"immediate", "recover", "late_error"}:
        result = await agent.propose(**args)
        count = 2 if mode == "recover" else 1
        assert len(queries) == count
        assert result["num_turns"] == 2 * count
        assert result["total_cost_usd"] == 0.25 * (count + 1)
    else:
        match = {
            "exhausted": "3 follow-ups",
            "sdk_error": "error_max_turns",
            "timeout": "session timed out",
        }[mode]
        with pytest.raises(AgentError, match=match):
            await agent.propose(**args)
        assert len(queries) == {"exhausted": 4, "sdk_error": 1, "timeout": 2}[mode]
    # Neither the Lando token nor the run's upload grant reach the model's shell.
    assert options[0].env == dict.fromkeys(agent.HIDDEN_ENV, "")
    assert {"LANDO_ACCESS_TOKEN", "RESULTS_POLICY_URL", "RESULTS_POLICY_FIELDS"} <= set(
        agent.HIDDEN_ENV
    )
    assert {"AskUserQuestion", "Task"} <= set(options[0].disallowed_tools)


@pytest.mark.parametrize(
    "failure", ["stream", "after_result", "eof", "shutdown", "timeout", "cancel"]
)
@pytest.mark.parametrize("submitted", [False, True])
@pytest.mark.parametrize("has_candidate", [False, True])
async def test_preserves_recorded_proposal_after_sdk_failure(
    tmp_path, monkeypatch, failure, submitted, has_candidate
):
    context = None
    proposal = dict(
        has_candidate=has_candidate,
        summary="Investigation complete",
        regression_location="layout/grid.cpp",
        root_cause="Cause",
        profile_evidence="Evidence",
        correctness_checks=["lint passed"],
    )

    def build_sdk_server(name, ctx, tools):
        nonlocal context
        context = ctx

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            if failure == "shutdown":
                raise CLIConnectionError("SDK disconnect failed")

        async def query(self, prompt):
            pass

        async def receive_response(self):
            if submitted:
                await agent.submit_result(context, proposal)
            if failure == "cancel":
                raise asyncio.CancelledError()
            if failure == "timeout":
                raise TimeoutError("SDK response timed out")
            if failure == "eof":
                return
            if failure == "stream":
                raise CLIJSONDecodeError("invalid JSON", ValueError("truncated"))
            yield ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="same-session",
                total_cost_usd=0.25,
            )
            if failure == "after_result":
                raise CLIJSONDecodeError("invalid JSON", ValueError("truncated"))

    monkeypatch.setattr(agent, "build_sdk_server", build_sdk_server)
    monkeypatch.setattr(agent, "ClaudeSDKClient", Client)
    monkeypatch.setattr(agent, "Reporter", MagicMock())
    args = dict(
        repo=tmp_path,
        evidence=tmp_path,
        previous=[],
        model=None,
        max_turns=500,
        effort="high",
        log=tmp_path / "log",
    )
    if submitted and failure != "cancel":
        result = await agent.propose(**args)
        assert {key: result[key] for key in proposal} == proposal
        usage_available = failure in {"after_result", "shutdown"}
        assert result["num_turns"] == (1 if usage_available else 0)
        assert result["total_cost_usd"] == (0.25 if usage_available else 0)
    else:
        error = {
            "stream": CLIJSONDecodeError,
            "after_result": CLIJSONDecodeError,
            "eof": AgentError,
            "shutdown": CLIConnectionError,
            "timeout": AgentError,
            "cancel": asyncio.CancelledError,
        }[failure]
        with pytest.raises(error):
            await agent.propose(**args)


async def test_candidate_tool_uses_shared_schema_and_error_adapter():
    from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

    ctx = agent.CandidateContext()
    server = agent.build_sdk_server("candidate", ctx, agent.tools_in(agent.__name__))[
        "instance"
    ]
    listed = await server.request_handlers[ListToolsRequest](
        ListToolsRequest(method="tools/list")
    )
    definition = listed.root.tools[0]
    assert definition.name == "submit_result"
    assert "proposal" in definition.inputSchema["properties"]
    proposal = dict(
        has_candidate=False,
        summary="No plausible fix",
        regression_location="Unknown: missing profile symbols",
        root_cause="Uncertain",
        profile_evidence="Missing",
        correctness_checks=[],
    )
    call = CallToolRequest(
        method="tools/call",
        params=CallToolRequestParams(
            name="submit_result",
            arguments={
                "proposal": {
                    key: value
                    for key, value in proposal.items()
                    if key != "regression_location"
                }
            },
        ),
    )
    response = await server.request_handlers[CallToolRequest](call)
    assert response.root.isError
    assert ctx.proposal is None

    call.params.arguments = {"proposal": proposal}
    response = await server.request_handlers[CallToolRequest](call)
    assert not response.root.isError
    # next_steps is optional and defaults to empty.
    assert ctx.proposal.model_dump() == {**proposal, "next_steps": ""}
