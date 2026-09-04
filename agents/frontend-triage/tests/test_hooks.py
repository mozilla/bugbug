"""Tests for the record-time limits on what this agent may write to a bug.

The hooks run inside the agent as the action is recorded, so a rejection reaches the
model as a tool error and the action never lands in summary.json — see
hackbot_agents/frontend_triage/hooks.py.
"""

import pytest
from agent_tools.registry import ToolError
from hackbot_agents.frontend_triage.config import ENABLED_ACTION_TYPES
from hackbot_agents.frontend_triage.hooks import (
    add_comment_hook,
    area_guidance_hook,
    severity_block_hook,
)
from hackbot_runtime.actions import ActionsRecorder
from hackbot_runtime.actions.claude_sdk import actions_to_tool_names

BUG = 2014702


def _recorder():
    rec = ActionsRecorder()
    rec.add_hook("bugzilla.add_comment", add_comment_hook(rec, BUG))
    return rec


def _comment(rec, bug_id=BUG, **params):
    return rec.record(
        "bugzilla.add_comment",
        {"bug_id": bug_id, "text": "hi", **params},
        reasoning="rule X",
    )


def test_the_agent_is_given_no_tool_that_changes_a_bug_field():
    # What makes dropping `severity` structural rather than something the prompt asks
    # for: `ENABLED_ACTION_TYPES` decides which tools the actions server exposes at all.
    assert ENABLED_ACTION_TYPES == ["bugzilla.add_comment"]
    tools = actions_to_tool_names(ENABLED_ACTION_TYPES)
    assert not [t for t in tools if "update_bug" in t], tools


def test_a_private_comment_is_refused():
    rec = _recorder()
    with pytest.raises(ToolError):
        _comment(rec, is_private=True)
    assert rec.actions == []


def test_a_public_comment_is_recorded():
    rec = _recorder()
    _comment(rec, is_private=False)
    assert rec.actions[0]["type"] == "bugzilla.add_comment"


def test_a_comment_against_another_bug_is_refused():
    for bug_id in (999, "2014702", None, f"{BUG} "):
        rec = _recorder()
        with pytest.raises(ToolError):
            _comment(rec, bug_id=bug_id)
        assert rec.actions == []


def test_only_one_comment_may_be_recorded():
    # A model told to write "a single brief comment" can still record fifty; the
    # prompt is not what caps this.
    rec = _recorder()
    _comment(rec)
    with pytest.raises(ToolError):
        _comment(rec)
    assert len(rec.actions) == 1


def test_a_refused_comment_does_not_use_up_the_allowance():
    # The count is taken from what was actually recorded, so a rejected attempt
    # leaves the agent free to retry with a corrected call.
    rec = _recorder()
    with pytest.raises(ToolError):
        _comment(rec, is_private=True)
    _comment(rec)
    assert len(rec.actions) == 1


def test_a_comment_may_declare_its_severity_only_once():
    plan = "Root cause is a stale selector in content-area.css."
    block = "\n\nSuggested severity: S4. Cosmetic only, no functional impact."

    # Absent is deliberate — a run with low severity confidence omits the block.
    severity_block_hook({"params": {"text": plan}})
    severity_block_hook({"params": {"text": plan + block}})

    # An ordinary mention is not a declaration; the agent quotes reporters and argues
    # about levels in its analysis, and only a line *starting* the declaration counts.
    severity_block_hook(
        {"params": {"text": "The reporter argues this is S1, but see below." + block}}
    )

    with pytest.raises(ToolError):
        severity_block_hook({"params": {"text": plan + block + block}})


def _cite(path: str) -> str:
    """A comment citing ``path`` the way the prompt asks for it."""
    return f"The fault is in [{path}]({{{{searchfox.permalink}}}}/{path})."


def _area_hook(*loaded: str):
    hook = area_guidance_hook(set(loaded))
    return lambda text: hook({"params": {"bug_id": BUG, "text": text}})


def test_a_comment_citing_an_unloaded_area_is_refused():
    # A New Tab Page bug that turns out to be the installer. The agent acts on this
    # in-run, so the message has to name the area and the tool.
    with pytest.raises(ToolError) as e:
        _area_hook("Desktop frontend")(
            _cite("browser/installer/windows/nsis/installer.nsi")
        )
    assert "Windows installer" in str(e.value)
    assert "load_area_guidance" in str(e.value)


def test_a_comment_citing_a_loaded_area_passes():
    _area_hook("Desktop frontend")(_cite("browser/components/tabbrowser/tabgroup.js"))


def test_an_area_loaded_mid_run_passes():
    # `load_area_guidance` adds to the same set the hook reads, so the retry after a
    # refusal succeeds. If this ever decoupled, the agent would be stuck in a loop it
    # cannot exit and the run would burn its turns.
    loaded = {"Desktop frontend"}
    hook = area_guidance_hook(loaded)
    body = _cite("browser/installer/windows/nsis/installer.nsi")
    with pytest.raises(ToolError):
        hook({"params": {"bug_id": BUG, "text": body}})
    loaded.add("Windows installer")
    hook({"params": {"bug_id": BUG, "text": body}})


def test_a_comment_citing_no_known_area_passes():
    # A Graphics bug has no file to load, and it triages fine today off the source tree
    # and Searchfox. Refusing here would fail the run over something the agent cannot
    # satisfy -- it would retry forever against guidance that does not exist.
    _area_hook("Desktop frontend")(_cite("gfx/thebes/gfxPlatform.cpp"))
