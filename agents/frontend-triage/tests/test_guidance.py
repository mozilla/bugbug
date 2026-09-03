"""Tests for the `load_component_guidance` tool.

The tool exists so the load is observable to `hooks.component_guidance_hook`, not just so
the agent can read something. Its side effect on `GuidanceContext.loaded` is therefore the
part that matters, and the part that had no coverage before.
"""

from pathlib import Path

import pytest
from agent_tools.registry import ToolError
from hackbot_agents.frontend_triage.docs import DocRef
from hackbot_agents.frontend_triage.guidance import (
    GuidanceContext,
    load_component_guidance,
)

_KNOWN = (
    DocRef(
        tree="browser/installer/windows/docs",
        path="browser/installer/windows/installer/",
    ),
)


def _ctx(*loaded: str) -> GuidanceContext:
    # A path that is not a checkout: `known_docs` is pre-resolved, so nothing here should
    # reach the filesystem. If a change makes it grep, these tests get slower and stop
    # being hermetic, which is worth noticing.
    return GuidanceContext(
        repo=Path("/nonexistent"), loaded=set(loaded), known_docs=_KNOWN
    )


async def test_loading_a_component_records_it_for_the_hook():
    # The shared-set contract. `hooks.component_guidance_hook` reads this same object, so
    # this is what makes the retry after a refused comment succeed.
    ctx = _ctx("Firefox :: New Tab Page")
    result = await load_component_guidance(
        ctx, product="Firefox", component="Installer"
    )
    assert result["component"] == "Firefox :: Installer"
    assert ctx.loaded == {"Firefox :: New Tab Page", "Firefox :: Installer"}


async def test_the_result_carries_the_notes_and_the_doc_url():
    result = await load_component_guidance(
        ctx := _ctx(), product="Firefox", component="Installer"
    )
    assert "empty `relevant_tests`" in result["notes"]
    assert result["docs"] == [
        {
            "tree": "browser/installer/windows/docs",
            "url": "https://firefox-source-docs.mozilla.org/browser/installer/windows/installer/",
        }
    ]
    assert ctx.loaded == {"Firefox :: Installer"}


async def test_the_product_and_component_are_matched_case_insensitively():
    # The model is copying two fields out of Bugzilla and the prompt, and a case
    # mismatch is not a reason to make it retry a lookup it got substantively right.
    result = await load_component_guidance(
        _ctx(), product="firefox", component="installer"
    )
    assert result["component"] == "Firefox :: Installer"


async def test_surrounding_whitespace_is_ignored():
    result = await load_component_guidance(
        _ctx(), product=" Firefox ", component=" Installer "
    )
    assert result["component"] == "Firefox :: Installer"


def test_a_context_cannot_be_built_without_resolved_docs():
    # `known_docs` used to default to `()` and be tested for truthiness, so a checkout
    # that legitimately has no docs looked like a cache miss and re-ran the subprocess --
    # with its 60s timeout -- on every call inside an async tool. Requiring the field is
    # what makes that unrepresentable, so this fails if a default comes back.
    #
    # Asserting on the *result* would have no teeth: the old code returned an empty docs
    # list here too. `guidance.py` does not import `registrations` any more, so there is
    # nothing left to re-search with.
    with pytest.raises(TypeError):
        GuidanceContext(repo=Path("/nonexistent"), loaded=set())


async def test_a_checkout_with_no_docs_yields_no_links():
    ctx = GuidanceContext(repo=Path("/nonexistent"), loaded=set(), known_docs=())
    result = await load_component_guidance(
        ctx, product="Firefox", component="Installer"
    )
    assert result["docs"] == []
    assert result["notes"]


async def test_an_untriaged_component_is_refused_with_the_list_of_real_ones():
    # The agent recovers from this in-run, so the error has to say what it could have
    # asked for. Nothing is marked loaded, or a failed lookup would silently unblock the
    # hook.
    ctx = _ctx()
    with pytest.raises(ToolError) as e:
        await load_component_guidance(ctx, product="Core", component="Graphics")
    assert e.value.payload["error"] == "unknown_component"
    assert e.value.payload["requested"] == "Core :: Graphics"
    assert "Firefox :: Installer" in e.value.payload["known_components"]
    assert ctx.loaded == set()
