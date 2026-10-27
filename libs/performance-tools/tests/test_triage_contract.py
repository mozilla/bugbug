"""The triage step's contract with the vendored triage-perf-bug skill.

The prompt must defer to the skill's agent mode, map its JSON envelope, and not
copy its criteria; the last test reads the live calculator checkout so an
upstream change that breaks the contract fails here, not on a paid run.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_the_prompt_defers_to_agent_mode_rather_than_overriding_the_skill():
    """The prompt no longer overrides what agent mode now guarantees.

    Before the skill had an agent mode (2026-08-25) this prompt had to tell the
    model to ignore Step 5's Markdown fences and `### Comment N` markers. Agent
    mode emits one JSON object, so those overrides are gone -- a prompt fighting
    a skill that no longer needs overriding is how the two drift apart.

    The BEHAVIOUR is still required; it is just guaranteed upstream now. See
    test_the_skill_actually_implements_agent_mode below, which reads the skill.
    """
    from performance_tools.triage import TRIAGE_TASK_DETAILS

    assert "AGENT MODE" in TRIAGE_TASK_DETAILS.upper()
    # No longer our job to say these -- the skill's agent mode does.
    assert "### Comment" not in TRIAGE_TASK_DETAILS
    assert "four-backtick" not in TRIAGE_TASK_DETAILS
    assert "no write access" not in TRIAGE_TASK_DETAILS


def test_the_envelope_mapping_is_spelled_out():
    """Agent mode returns {calculator, severity}; TriageResult is flat.

    The model has to be told which nested key lands on which field, or it will
    guess -- and `selections` in particular must arrive unchanged, since that is
    what perf-triage-validator diffs against a human adjudication.
    """
    from performance_tools.triage import TRIAGE_TASK_DETAILS

    assert '"calculator"' in TRIAGE_TASK_DETAILS
    assert "calculator.selections" in TRIAGE_TASK_DETAILS
    assert "calculator.url" in TRIAGE_TASK_DETAILS


def test_the_prompt_does_not_restate_the_skills_judgement_criteria():
    """The criteria live upstream, where the validator's corpus measures them.

    Copying them here would fork the calibration: our copy drifts, and
    perf-triage-validator ends up measuring something we no longer run.
    """
    from performance_tools.triage import TRIAGE_TASK_DETAILS

    low = TRIAGE_TASK_DETAILS.lower()
    # Phrases from the skill's Step 2 that must NOT be duplicated here.
    for phrase in (
        "reflects only where it was tested",
        "renders site effectively unusable",
        "primary concern",
        "hundreds of tabs",
    ):
        assert phrase not in low, f"step 2 criteria copied into our prompt: {phrase}"


def test_the_task_sets_agent_mode_itself():
    """Without PERF_TRIAGE_AGENT_MODE the skill runs interactive mode.

    That failure is quiet and expensive: it walks the profile a second time and
    returns four-backtick-fenced Markdown that TriageResult cannot represent. So
    the task sets it rather than trusting compose.yml, where a missing entry
    would be invisible.
    """
    from performance_tools.triage.task import PERF_TRIAGE_AGENT_MODE_ENV

    src = Path(
        __import__("performance_tools.triage.task", fromlist=["x"]).__file__
    ).read_text()
    assert PERF_TRIAGE_AGENT_MODE_ENV in src
    assert "options.env" in src, "agent mode must reach the spawned subprocess"


def test_the_skill_actually_implements_agent_mode():
    """Reads the real SKILL.md, so a dropped agent mode fails here, not at $2/bug.

    Skipped when the calculator checkout is absent -- CI does not have it, and a
    missing sibling repo is not a defect in this one.
    """
    skill = (
        Path.home()
        / "projects/performance-triage-calculator/.claude/skills/triage-perf-bug/SKILL.md"
    )
    if not skill.exists():
        pytest.skip("calculator checkout not present")
    text = skill.read_text()
    assert "PERF_TRIAGE_AGENT_MODE=1" in text
    # A caller that analyses the profile itself depends on the opt-out that keeps
    # the skill from analysing it too (PerfTriage sets it by default).
    assert "PERF_TRIAGE_SKIP_PROFILE=1" in text, (
        "Step 3 must be skippable in agent mode"
    )
    assert '"calculator"' in text, "Step 5 must emit the nested calculator object"
    assert '"unestablished"' in text and '"profile"' in text
    assert '"related"' in text


def test_the_prompt_forbids_reverse_engineering_a_rating():
    """The validator measures the agent's flag choices against human adjudication.

    An agent that re-picks flags until the rating "looks right" produces output
    that agrees with itself and teaches the validator nothing. Patricia,
    2026-08-25: pages-affected calibration is already being tuned with engineer
    feedback there, so this step must report what the criteria produce, not what
    it would prefer.
    """
    from performance_tools.triage import TRIAGE_TASK_DETAILS

    assert "DO NOT RE-PICK FLAGS" in TRIAGE_TASK_DETAILS
    assert "run the calculator once" in TRIAGE_TASK_DETAILS
    # and it must not point the model at re-running with different inputs
    assert "the fix is different flags" not in TRIAGE_TASK_DETAILS
