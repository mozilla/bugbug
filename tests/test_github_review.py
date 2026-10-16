"""GitHub snapshots and context must consistently reflect the reviewed revision."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from bugbug.tools.code_review import langchain_tools, review_context
from bugbug.tools.core.platforms.github import (
    GitHubCodeContext,
    GitHubPatch,
    GitHubSnapshotChanged,
)

HEAD = "a" * 40
BASE = "b" * 40
MERGE_BASE = "c" * 40
DIFF = "--- a/new.rs\n+++ b/new.rs\n@@ -1 +1 @@\n-old\n+new_symbol();\n"


def pr_data():
    return {
        "number": 15,
        "title": "Change the client",
        "body": "Some details",
        "state": "open",
        "draft": False,
        "commits": 2,
        "created_at": "2026-10-07T10:00:00Z",
        "html_url": "https://github.com/owner/repo/pull/15",
        "head": {"sha": HEAD, "repo": {"full_name": "fork/repo"}},
        "base": {"sha": BASE, "repo": {"private": False}},
    }


def response(body, status=200):
    kwargs = {"text": body} if isinstance(body, str) else {"json": body}
    return httpx.Response(
        status, request=httpx.Request("GET", "https://api.github.com/test"), **kwargs
    )


class SnapshotAPI:
    def __init__(self):
        self.pr = pr_data()
        self.requests = []

    async def request(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        if path.endswith("pulls/15"):
            return response(deepcopy(self.pr))
        if "/compare/" in path:
            if kwargs.get("headers"):
                return response(DIFF)
            return response({"merge_base_commit": {"sha": MERGE_BASE}})
        if path.endswith("pulls/15/commits"):
            return response(
                [
                    {"sha": "d" * 40, "commit": {"message": "First unit"}},
                    {"sha": HEAD, "commit": {"message": "Second unit"}},
                ]
            )
        if "/commits/" in path:
            return response(DIFF)
        if "/contents/" in path:
            return response("new_symbol();\n")
        if path == "search/code":
            return response({"items": [{"path": "unchanged.rs"}]})
        raise AssertionError(path)


@pytest.mark.asyncio
async def test_snapshot_pins_merge_base_head_and_trusted_context():
    api = SnapshotAPI()
    snapshot = await GitHubPatch.load(api, "owner/repo", 15, HEAD)
    assert await snapshot.get_base_revision() == MERGE_BASE
    assert await snapshot.github_repo_ref() == ("owner/repo", BASE)
    assert snapshot.commit_context.index("First unit") < snapshot.commit_context.index(
        "Second unit"
    )
    await snapshot.get_old_file("new.rs")
    await snapshot.get_new_file("new.rs")
    file_requests = [r for r in api.requests if "/contents/" in r[1]]
    assert file_requests[0][2]["params"]["ref"] == MERGE_BASE
    assert file_requests[1][2]["params"]["ref"] == HEAD


@pytest.mark.asyncio
async def test_snapshot_rejects_push_during_fetch():
    api = SnapshotAPI()
    original = api.request

    async def request(method, path, **kwargs):
        if "/commits/" in path:
            api.pr["head"]["sha"] = "e" * 40
        return await original(method, path, **kwargs)

    api.request = request
    with pytest.raises(GitHubSnapshotChanged):
        await GitHubPatch.load(api, "owner/repo", 15, HEAD)


@pytest.mark.asyncio
async def test_snapshot_survives_base_tip_movement():
    api = SnapshotAPI()
    original = api.request

    async def request(method, path, **kwargs):
        if "/commits/" in path:
            api.pr["base"]["sha"] = "e" * 40
        return await original(method, path, **kwargs)

    api.request = request
    await GitHubPatch.load(api, "owner/repo", 15, HEAD)


@pytest.mark.asyncio
async def test_search_includes_new_symbols_and_reads_discovery_at_head():
    api = SnapshotAPI()
    snapshot = await GitHubPatch.load(api, "owner/repo", 15)
    source = GitHubCodeContext(snapshot)
    result = await source.search_text("new_symbol")
    assert "new.rs:1: new_symbol();" in result
    assert "unchanged.rs:1: new_symbol();" in result
    await source.search_text("new_symbol")
    assert sum(path == "search/code" for _, path, _ in api.requests) == 1
    assert all(
        kwargs["params"]["ref"] == HEAD
        for _, path, kwargs in api.requests
        if "/contents/" in path
    )


@pytest.mark.asyncio
async def test_search_still_reads_changed_files_when_discovery_is_rate_limited():
    api = SnapshotAPI()
    snapshot = await GitHubPatch.load(api, "owner/repo", 15)
    original = api.request

    async def request(method, path, **kwargs):
        if path == "search/code":
            response({}, 403).raise_for_status()
        return await original(method, path, **kwargs)

    api.request = request
    result = await GitHubCodeContext(snapshot).search_text("new_symbol")
    assert "only changed files" in result
    assert "new.rs:1: new_symbol();" in result


@pytest.mark.asyncio
async def test_expand_context_never_falls_back_to_searchfox_for_github():
    api = SnapshotAPI()
    snapshot = await GitHubPatch.load(api, "owner/repo", 15)
    source = GitHubCodeContext(snapshot)
    runtime = SimpleNamespace(
        context=langchain_tools.CodeReviewContext(snapshot, source)
    )
    with (
        patch.object(langchain_tools, "get_runtime", return_value=runtime),
        patch.object(
            langchain_tools,
            "_get_client",
            side_effect=AssertionError("Wrong repository"),
        ),
    ):
        assert (
            await langchain_tools.expand_context.coroutine("new.rs", 1, 1)
            == "1| new_symbol();"
        )
        assert "new_symbol" in await langchain_tools.search_identifier.coroutine(
            "new_symbol"
        )
    assert {t.name for t in langchain_tools.context_tools_for(source)} == {
        "expand_context",
        "search_text",
        "search_identifier",
    }


@pytest.mark.asyncio
async def test_optional_context_loads_authenticated_files_at_trusted_revision():
    config = """version = 1
[[rules]]
name = "rust"
    [rules.when.any_file]
    include = ["*.rs"]
[[rules.load]]
type = "file"
path = "REVIEW.md"
"""
    loader = AsyncMock(side_effect=[config, "Project review guidance"])
    snapshot = SimpleNamespace(
        raw_diff=DIFF,
        bug_component=AsyncMock(return_value=None),
        get_review_context_file=loader,
    )
    items = await review_context.load_external_content_for_diff(
        DIFF, "owner/repo", review_context_branch=BASE, patch=snapshot
    )
    assert len(items) == 1
    assert items[0].body == "Project review guidance"
    assert loader.await_args_list[0].args == ("owner/repo", BASE, "review-context.toml")
    assert loader.await_args_list[1].args == ("owner/repo", BASE, "REVIEW.md")


@pytest.mark.asyncio
async def test_context_tools_use_the_provider_in_a_real_graph_runtime():
    from langgraph.graph import END, START, MessagesState, StateGraph

    api = SnapshotAPI()
    snapshot = await GitHubPatch.load(api, "owner/repo", 15)
    source = GitHubCodeContext(snapshot)
    result = []

    async def node(state):
        result.append(
            await langchain_tools.search_text.ainvoke({"query": "new_symbol"})
        )
        return {}

    builder = StateGraph(
        MessagesState, context_schema=langchain_tools.CodeReviewContext
    )
    builder.add_node("search", node)
    builder.add_edge(START, "search")
    builder.add_edge("search", END)
    with patch.object(
        langchain_tools, "_get_client", side_effect=AssertionError("Wrong repository")
    ):
        await builder.compile().ainvoke(
            {"messages": []},
            context=langchain_tools.CodeReviewContext(snapshot, source),
        )
    assert "new.rs:1: new_symbol();" in result[0]
