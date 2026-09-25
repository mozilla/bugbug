"""Tests for prompt rendering.

All pure: these turn inputs into prompt text and never touch the filesystem, so
``scratch_out`` is passed as a value. Diff writing is covered in
``test_uplift_diffs``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from hackbot_agents.uplift_merge_conflict_resolver.agent import (
    build_user_prompt,
    load_workflow,
    render_bug_block,
    render_sources,
)
from hackbot_agents.uplift_merge_conflict_resolver.models import (
    FetchedDiff,
    GitSource,
    PhabricatorSource,
)
from hackbot_runtime import AgentError

SCRATCH = Path("/scratch/out")
COMMIT = "a" * 40
BASE = "b" * 40


def fetched_for(
    *sources,
    author: str | None = "Dev <dev@example.com>",
    base_commit: str | None = BASE,
):
    """One entry per source, as `render_sources` reads them.

    A `GitSource` has nothing to fetch, so it contributes `None`.
    """
    return [
        None
        if isinstance(source, GitSource)
        else FetchedDiff(
            path=source.diff_path(SCRATCH, source.diff_id or 1),
            author=author,
            base_commit=base_commit,
        )
        for source in sources
    ]


def test_render_sources_describes_a_mixed_stack():
    """One work item per source, in order, each with what its kind needs."""
    sources = [
        GitSource(commit=COMMIT),
        PhabricatorSource(revision_id=88, diff_id=500),
    ]

    git_line, phab_line = render_sources(sources, fetched_for(*sources)).splitlines()

    assert git_line.startswith("1."), "Work items are numbered in the order applied."
    assert f"`{COMMIT}`" in git_line, "A git source names the commit to pick."
    assert "cherry-pick" in git_line, "A git source is applied as a cherry-pick."

    assert phab_line.startswith("2."), "Numbering follows input order."
    assert "D88" in phab_line, "A Phabricator source names its revision."
    assert str(SCRATCH / "D88-500.diff") in phab_line, (
        "It points at the path its diff was actually written to."
    )
    assert f"`{BASE}`" in phab_line, (
        "It names the base commit, which `git apply --3way` needs fetched to "
        "find the blobs the diff was built from."
    )
    assert "Commit it on its own" in phab_line, (
        "A diff carries no commit of its own, so the agent is told to make one "
        "per source -- the stack is re-created from those commits."
    )


def test_render_sources_says_what_phabricator_did_not_record():
    """A base commit is not guaranteed, and guessing one lands a bad patch."""
    sources = [PhabricatorSource(revision_id=88)]

    text = render_sources(sources, fetched_for(*sources, base_commit=None))

    assert "no base commit" in text, (
        "Without a base commit the agent is told, not left to wonder why the "
        "three-way merge found nothing."
    )


def test_render_sources_refuses_a_phabricator_source_that_was_not_fetched():
    # Rendering a path the diff was never written to would point the agent at a
    # file that does not exist, so this is a failure rather than a guess.
    with pytest.raises(AgentError, match="must be fetched"):
        render_sources([PhabricatorSource(revision_id=88)], [None])


def test_render_bug_block_is_present_only_when_there_is_a_bug():
    assert render_bug_block(None) == "", (
        "With no bug id, the originating-bug block should be empty."
    )

    block = render_bug_block(1500000)
    assert "1500000" in block, "The bug block should name the bug id."
    assert "get_bugzilla_bug" in block, "The bug block should point at the MCP tool."


def test_the_workflow_holds_no_per_run_detail():
    """Identical every run, which is what prompt caching reuses."""
    prompt = load_workflow()

    assert re.search(r"\{[a-z_]+\}", prompt) is None, (
        "An unrendered placeholder means per-run detail leaked into the prefix."
    )
    assert prompt == load_workflow(), "The workflow should not vary."
    assert '"resolved": true' in prompt, (
        "The report shape is stable guidance, so it belongs here -- and with "
        "nothing to `.format`, its braces need no escaping."
    )
    assert "How to apply each patch" in prompt, (
        "How to apply a source is guidance, not a detail of one run."
    )
    assert "get_phabricator_revision" in prompt, (
        "A raw diff carries no commit message, so the agent is told where to "
        "read the revision's own rather than inventing one."
    )


def test_the_task_prompt_holds_the_run(tmp_path):
    sources = [GitSource(commit=COMMIT), PhabricatorSource(revision_id=77)]

    prompt = build_user_prompt(
        target_branch="release",
        sources=sources,
        bug_id=42,
        scratch_out=tmp_path,
        fetched=fetched_for(*sources),
    )

    assert prompt.startswith(load_workflow()), (
        "The workflow leads the prompt, so the cacheable prefix comes first."
    )
    assert "release" in prompt, "The task should name the branch to uplift onto."
    assert f"`{COMMIT}`" in prompt and "D77" in prompt, (
        "Both sources should reach the task, which is what the agent works from."
    )
    assert "bug 42" in prompt, "The originating bug belongs with the task."
    assert "\n\n## Originating bug\n\n" in prompt, (
        "The bug section stands on its own, with a blank line on either side."
    )
    assert "\n\n\n" not in prompt, (
        "And no doubled blank line where the section was spliced in."
    )
    assert f"{tmp_path}/report.json" in prompt, (
        "The task names where to write the report, since the path is per-run."
    )
    assert f"{tmp_path}/summary.md" in prompt, "And the summary beside it."


def test_the_task_prompt_omits_the_bug_section_without_a_bug(tmp_path):
    sources = [GitSource(commit=COMMIT)]

    prompt = build_user_prompt(
        target_branch="beta",
        sources=sources,
        bug_id=None,
        scratch_out=tmp_path,
        fetched=fetched_for(*sources),
    )

    assert "Originating bug" not in prompt, (
        "With no bug there should be no empty section left behind."
    )
    assert "\n\n\n\n" not in prompt, (
        "And no run of blank lines where the section would have been."
    )
