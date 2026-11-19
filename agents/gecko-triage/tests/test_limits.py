"""Tests for the step and cost limits file."""

import pytest
from hackbot_agents.gecko_triage.config import SHARED_DIR
from hackbot_agents.gecko_triage.teamconfig import SESSIONS, Limits, load_limits
from pydantic import ValidationError

TURNS = {s: 10 for s in SESSIONS}
EFFORT = {s: "high" for s in SESSIONS}


def _limits(turns=None, budget=5.0, effort=None):
    return Limits.model_validate(
        {
            "turns": TURNS if turns is None else turns,
            "budget": {"max_usd": budget},
            "effort": EFFORT if effort is None else effort,
        }
    )


def test_the_shipped_file_sets_a_limit_for_every_session():
    limits = load_limits(SHARED_DIR / "limits.toml")
    for session in SESSIONS:
        assert limits.turn_limit(session)
    assert limits.max_budget_usd


def test_zero_means_no_limit():
    limits = _limits({**TURNS, "profile_analyst": 0}, budget=0)
    assert limits.turn_limit("profile_analyst") is None
    assert limits.turn_limit("main") == 10
    assert limits.max_budget_usd is None


@pytest.mark.parametrize(
    "turns",
    [
        {k: v for k, v in TURNS.items() if k != "main"},
        {**TURNS, "extra": 5},
        {**TURNS, "main": -1},
    ],
    ids=["missing session", "unknown session", "negative"],
)
def test_a_bad_turns_table_is_refused(turns):
    with pytest.raises(ValidationError):
        _limits(turns)


def test_a_negative_budget_is_refused():
    with pytest.raises(ValidationError):
        _limits(budget=-1)


def test_another_file_can_turn_every_limit_off(tmp_path):
    path = tmp_path / "limits.toml"
    path.write_text(
        "[turns]\n"
        + "".join(f"{s} = 0\n" for s in SESSIONS)
        + "\n[budget]\nmax_usd = 0\n"
        + "\n[effort]\n"
        + "".join(f'{s} = "high"\n' for s in SESSIONS)
    )
    limits = load_limits(path)
    assert all(limits.turn_limit(s) is None for s in SESSIONS)
    assert limits.max_budget_usd is None


def test_the_turns_table_names_every_session():
    from hackbot_agents.gecko_triage.teamconfig import Turns

    assert tuple(Turns.model_fields) == SESSIONS


def test_turn_limits_file_replaces_the_shipped_file(tmp_path):
    import subprocess
    import sys

    path = tmp_path / "limits.toml"
    path.write_text(
        "[turns]\n"
        + "".join(f"{s} = 0\n" for s in SESSIONS)
        + "\n[budget]\nmax_usd = 0\n"
        + "\n[effort]\n"
        + "".join(f'{s} = "high"\n' for s in SESSIONS)
    )
    code = (
        "from hackbot_agents.gecko_triage import agent; "
        "print(agent.DEFAULT_MAX_TURNS, agent.DEFAULT_MAX_BUDGET_USD, "
        "agent.make_profile_analyst().maxTurns)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={**__import__("os").environ, "TURN_LIMITS_FILE": str(path)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.split() == ["None", "None", "None"]


def test_the_shipped_file_sets_an_effort_for_every_session():
    limits = load_limits(SHARED_DIR / "limits.toml")
    for session in SESSIONS:
        assert limits.effort_for(session) in {"low", "medium", "high", "xhigh", "max"}
    # The analysis sessions think hardest; the drafting and search sessions less.
    for session in (
        "bug_investigator",
        "profile_analyst",
        "log_analyst",
        "close_verifier",
    ):
        assert limits.effort_for(session) == "high", session


def test_an_unknown_effort_is_refused():
    with pytest.raises(ValidationError):
        _limits(effort={**EFFORT, "main": "very high"})
    with pytest.raises(ValidationError):
        _limits(effort={s: "high" for s in SESSIONS if s != "main"})


def test_each_subagent_takes_its_effort_from_the_file():
    from hackbot_agents.gecko_triage import agent
    from hackbot_agents.gecko_triage.config import LIMITS, TRIAGE_SCOPE

    made = {
        "investigator": agent.make_investigator(),
        "profile_analyst": agent.make_profile_analyst(),
        "log_analyst": agent.make_log_analyst(),
        "bug_investigator": agent.make_bug_investigator(),
        "spec_checker": agent.make_spec_checker(),
        "duplicate_hunter": agent.make_duplicate_hunter(TRIAGE_SCOPE),
        "close_verifier": agent.make_close_verifier(),
    }
    for session, definition in made.items():
        assert definition.effort == LIMITS.effort_for(session), session
    assert LIMITS.effort_for("main") == agent.DEFAULT_EFFORT
