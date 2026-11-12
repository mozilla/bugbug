"""The tools, against a mocked hackbot-api."""

import json

import httpx
import pytest
from conftest import RUN_ID, UI, USER, run_doc
from fastmcp.exceptions import ToolError

STARTED = {"run_id": RUN_ID, "agent": "bug-fix", "status": "pending"}

TOOL_CALLS = {
    "list_agents": {},
    "start_run": {"agent": "bug-fix", "inputs": {"bug_id": 1}},
    "get_run": {"run_id": RUN_ID},
    "list_my_runs": {},
    "get_result": {"run_id": RUN_ID},
    "get_artifact_url": {"run_id": RUN_ID, "artifact": "changes/changes.patch"},
    "apply_actions": {"run_id": RUN_ID},
}


def _route_everything(api):
    api.route("POST", "/agents/bug-fix/runs", httpx.Response(201, json=STARTED))
    api.route(
        "GET",
        f"/runs/{RUN_ID}/artifacts/changes/changes.patch",
        httpx.Response(200, json={"url": "https://storage.example/signed"}),
    )
    api.route("POST", f"/runs/{RUN_ID}/actions/apply", httpx.Response(200, json=[]))


@pytest.mark.parametrize("tool", sorted(TOOL_CALLS))
async def test_every_call_is_on_behalf_of_the_authenticated_user(
    tool, api, signed_in, mcp_client
):
    _route_everything(api)
    signed_in(email="Dev@Mozilla.com")

    await mcp_client.call_tool(tool, TOOL_CALLS[tool])

    assert api.requests
    for request in api.requests:
        assert request.headers["X-On-Behalf-Of"] == USER
        assert request.headers["X-API-Key"] == "test-key"


async def test_no_tool_takes_an_identity_argument(server):
    for tool in await server.list_tools():
        params = set(tool.parameters.get("properties", {}))
        assert not params & {"email", "user", "on_behalf_of", "requested_by"}, tool.name


@pytest.mark.parametrize("tool", sorted(TOOL_CALLS))
async def test_tools_refuse_a_caller_without_a_mozilla_identity(
    tool, api, signed_in, mcp_client
):
    signed_in(google_user_data={"hd": "example.com"})

    with pytest.raises(ToolError, match="mozilla.com"):
        await mcp_client.call_tool(tool, TOOL_CALLS[tool])
    assert api.requests == []


async def test_start_run_holds_actions_for_review_by_default(
    api, signed_in, mcp_client
):
    api.route("POST", "/agents/bug-fix/runs", httpx.Response(201, json=STARTED))

    result = await mcp_client.call_tool(
        "start_run", {"agent": "bug-fix", "inputs": {"bug_id": 1}}
    )

    (post,) = api.sent("POST", "/agents/bug-fix/runs")
    assert json.loads(post.content) == {"bug_id": 1, "require_review": True}
    assert result.data["require_review"] is True


async def test_start_run_auto_apply_turns_review_off(api, signed_in, mcp_client):
    api.route("POST", "/agents/bug-fix/runs", httpx.Response(201, json=STARTED))

    await mcp_client.call_tool(
        "start_run", {"agent": "bug-fix", "inputs": {"bug_id": 1}, "auto_apply": True}
    )

    (post,) = api.sent("POST", "/agents/bug-fix/runs")
    assert json.loads(post.content)["require_review"] is False


@pytest.mark.parametrize("value", [True, False])
async def test_start_run_rejects_require_review_in_inputs(
    value, api, signed_in, mcp_client
):
    with pytest.raises(ToolError, match="auto_apply"):
        await mcp_client.call_tool(
            "start_run",
            {"agent": "bug-fix", "inputs": {"bug_id": 1, "require_review": value}},
        )
    assert api.sent("POST", "/agents/bug-fix/runs") == []


async def test_start_run_rejects_the_needinfo_input(api, signed_in, mcp_client):
    with pytest.raises(ToolError, match="bugzilla_needinfo_flag_id"):
        await mcp_client.call_tool(
            "start_run",
            {
                "agent": "bug-fix",
                "inputs": {
                    "bug_id": 1,
                    "comment": "please",
                    "bugzilla_needinfo_flag_id": 7,
                },
            },
        )
    assert api.sent("POST", "/agents/bug-fix/runs") == []


async def test_start_run_validates_inputs_against_the_agent_schema(
    api, signed_in, mcp_client
):
    with pytest.raises(ToolError, match="bug_id"):
        await mcp_client.call_tool(
            "start_run", {"agent": "bug-fix", "inputs": {"bug_id": "one"}}
        )
    with pytest.raises(ToolError, match="'bug_id' is a required property"):
        await mcp_client.call_tool("start_run", {"agent": "bug-fix", "inputs": {}})
    assert api.sent("POST", "/agents/bug-fix/runs") == []


async def test_start_run_rejects_an_unknown_agent(api, signed_in, mcp_client):
    with pytest.raises(ToolError, match="Unknown agent 'nope'.*bug-fix"):
        await mcp_client.call_tool("start_run", {"agent": "nope", "inputs": {}})


async def test_start_run_reports_the_api_validation_detail(api, signed_in, mcp_client):
    api.route(
        "POST",
        "/agents/bug-fix/runs",
        httpx.Response(422, json={"detail": "comment requires revision_id"}),
    )
    with pytest.raises(ToolError, match="comment requires revision_id"):
        await mcp_client.call_tool(
            "start_run", {"agent": "bug-fix", "inputs": {"bug_id": 1, "comment": "x"}}
        )


@pytest.mark.parametrize(("status_code", "is_new"), [(201, True), (200, False)])
async def test_start_run_says_whether_the_dedupe_key_matched_a_run(
    status_code, is_new, api, signed_in, mcp_client
):
    api.route("POST", "/agents/bug-fix/runs", httpx.Response(status_code, json=STARTED))

    result = await mcp_client.call_tool(
        "start_run",
        {"agent": "bug-fix", "inputs": {"bug_id": 1}, "dedupe_key": "bug-1"},
    )

    (post,) = api.sent("POST", "/agents/bug-fix/runs")
    assert post.url.params["dedupe_key"] == "bug-1"
    assert result.data["is_new"] is is_new
    assert result.data["run_id"] == RUN_ID
    assert result.data["url"] == f"{UI}/runs/{RUN_ID}"


async def test_get_run_returns_the_run_and_whether_it_finished(
    api, signed_in, mcp_client
):
    api.route(
        "GET", f"/runs/{RUN_ID}", httpx.Response(200, json=run_doc(status="running"))
    )

    result = await mcp_client.call_tool("get_run", {"run_id": RUN_ID})

    assert result.data["status"] == "running"
    assert result.data["finished"] is False
    assert "results_prefix" not in result.data


async def test_get_run_reports_an_unknown_run(api, signed_in, mcp_client):
    api.route(
        "GET", f"/runs/{RUN_ID}", httpx.Response(404, json={"detail": "Run not found"})
    )
    with pytest.raises(ToolError, match="not found"):
        await mcp_client.call_tool("get_run", {"run_id": RUN_ID})


async def test_list_my_runs_filters_on_the_caller(api, signed_in, mcp_client):
    result = await mcp_client.call_tool(
        "list_my_runs", {"agent": "bug-fix", "status": "running", "limit": 5}
    )

    (request,) = api.sent("GET", "/runs")
    assert dict(request.url.params) == {
        "agent": "bug-fix",
        "status": "running",
        "requested_by": USER,
        "limit": "5",
    }
    assert [run["run_id"] for run in result.data] == [RUN_ID]


async def test_list_my_runs_caps_the_limit(api, signed_in, mcp_client):
    with pytest.raises(ToolError):
        await mcp_client.call_tool("list_my_runs", {"limit": 101})


async def test_get_result_reports_summary_actions_as_pending_until_recorded(
    api, signed_in, mcp_client
):
    proposed = [
        {"type": "bugzilla.comment", "params": {"bug_id": 1, "text": "hi"}},
        {"type": "phabricator.submit", "params": {"bug_id": 1}, "ref": "patch"},
    ]
    api.route(
        "GET",
        f"/runs/{RUN_ID}",
        httpx.Response(
            200,
            json=run_doc(
                summary={"status": "ok", "findings": {"x": 1}, "actions": proposed}
            ),
        ),
    )

    result = await mcp_client.call_tool("get_result", {"run_id": RUN_ID})

    assert result.data["findings"] == {"x": 1}
    assert result.data["actions"] == [
        {
            "idx": 0,
            "type": "bugzilla.comment",
            "params": {"bug_id": 1, "text": "hi"},
            "ref": None,
            "status": "pending",
        },
        {
            "idx": 1,
            "type": "phabricator.submit",
            "params": {"bug_id": 1},
            "ref": "patch",
            "status": "pending",
        },
    ]


async def test_get_result_prefers_the_recorded_actions(api, signed_in, mcp_client):
    api.route(
        "GET",
        f"/runs/{RUN_ID}",
        httpx.Response(
            200,
            json=run_doc(
                summary={
                    "status": "ok",
                    "findings": {},
                    "actions": [{"type": "bugzilla.comment", "params": {}}],
                }
            ),
        ),
    )
    api.route(
        "GET",
        f"/runs/{RUN_ID}/actions",
        httpx.Response(
            200,
            json=[
                {
                    "idx": 0,
                    "type": "bugzilla.comment",
                    "params": {},
                    "status": "applied",
                    "result": {"comment_id": 9},
                }
            ],
        ),
    )

    result = await mcp_client.call_tool("get_result", {"run_id": RUN_ID})

    (action,) = result.data["actions"]
    assert action["status"] == "applied"
    assert action["result"] == {"comment_id": 9}


async def test_get_artifact_url_explains_how_to_apply_the_patch(
    api, signed_in, mcp_client
):
    _route_everything(api)

    result = await mcp_client.call_tool(
        "get_artifact_url", {"run_id": RUN_ID, "artifact": "changes/changes.patch"}
    )

    assert result.data["url"] == "https://storage.example/signed"
    assert "git am" in result.data["note"]
    assert "mbox" in result.data["note"]


async def test_get_artifact_url_has_no_note_for_other_artifacts(
    api, signed_in, mcp_client
):
    api.route(
        "GET",
        f"/runs/{RUN_ID}/artifacts/findings.md",
        httpx.Response(200, json={"url": "https://storage.example/f"}),
    )

    result = await mcp_client.call_tool(
        "get_artifact_url", {"run_id": RUN_ID, "artifact": "findings.md"}
    )

    assert "note" not in result.data


async def test_apply_actions_returns_each_outcome(api, signed_in, mcp_client):
    api.route(
        "POST",
        f"/runs/{RUN_ID}/actions/apply",
        httpx.Response(
            200,
            json=[
                {
                    "idx": 0,
                    "type": "bugzilla.comment",
                    "params": {"text": "hi"},
                    "status": "applied",
                    "result": {"comment_id": 9},
                },
                {
                    "idx": 1,
                    "type": "phabricator.submit",
                    "params": {},
                    "status": "failed",
                    "error": "conflict",
                },
            ],
        ),
    )

    result = await mcp_client.call_tool("apply_actions", {"run_id": RUN_ID})

    assert result.data["all_applied"] is False
    assert result.data["actions"] == [
        {
            "idx": 0,
            "type": "bugzilla.comment",
            "status": "applied",
            "result": {"comment_id": 9},
            "error": None,
        },
        {
            "idx": 1,
            "type": "phabricator.submit",
            "status": "failed",
            "result": None,
            "error": "conflict",
        },
    ]


@pytest.mark.parametrize("requested_by", ["someone.else@mozilla.com", None])
async def test_apply_actions_is_only_for_the_runs_requester(
    requested_by, api, signed_in, mcp_client
):
    api.route(
        "GET",
        f"/runs/{RUN_ID}",
        httpx.Response(200, json=run_doc(requested_by=requested_by)),
    )

    with pytest.raises(ToolError, match="Only the user who started this run"):
        await mcp_client.call_tool("apply_actions", {"run_id": RUN_ID})
    assert api.sent("POST", f"/runs/{RUN_ID}/actions/apply") == []


@pytest.mark.parametrize("status", ["pending", "running", "failed", "timed_out"])
async def test_apply_actions_needs_a_succeeded_run(status, api, signed_in, mcp_client):
    api.route(
        "GET", f"/runs/{RUN_ID}", httpx.Response(200, json=run_doc(status=status))
    )

    with pytest.raises(ToolError, match="succeeded"):
        await mcp_client.call_tool("apply_actions", {"run_id": RUN_ID})
    assert api.sent("POST", f"/runs/{RUN_ID}/actions/apply") == []
