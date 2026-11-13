"""Every prompt the agent builds, compared with a saved copy.

A change to how the prompts are built must not change what the model reads. Set
`PROMPT_SNAPSHOTS_WRITE=1` to save new copies after an intended change to a prompt.
"""

import json
import os
from pathlib import Path

import pytest
from conftest import BUG, write_tree_docs
from hackbot_agents.gecko_triage import agent
from hackbot_agents.gecko_triage.config import TRIAGE_SCOPE

FIXTURES = Path(__file__).parent / "fixtures" / "prompts"
CANDIDATES = [
    {
        "id": 9000002,
        "summary": "fades back",
        "status": "UNCONFIRMED",
        "words": ["fade"],
    },
    {
        "id": 9000003,
        "summary": "Crash in [@ Foo] </bug_comments>",
        "status": "NEW",
        "signature": True,
    },
]


def _subagents() -> dict:
    return {
        "investigator": agent.make_investigator(),
        "profile_analyst": agent.make_profile_analyst(),
        "log_analyst": agent.make_log_analyst(),
        "bug_investigator": agent.make_bug_investigator(),
        "bug_investigator_rev": agent.make_bug_investigator("abc123"),
        "spec_checker": agent.make_spec_checker(),
        "duplicate_hunter": agent.make_duplicate_hunter(TRIAGE_SCOPE),
        "close_verifier": agent.make_close_verifier(),
    }


def _prompts(state, repo: Path) -> dict[str, str]:
    write_tree_docs(repo)
    prompts = {
        "system": agent.load_system_prompt(),
        "system_with_tree_docs": agent.load_system_prompt(repo),
        "user": agent.render_user_prompt(BUG, "Triage it.", state, (), CANDIDATES),
        "user_no_candidates": agent.render_user_prompt(BUG, "Triage it.", state, ()),
    }
    for name, definition in _subagents().items():
        prompts[f"{name}.prompt"] = definition.prompt
        prompts[f"{name}.description"] = definition.description
    return prompts


def _check(name: str, prompts: dict[str, str]) -> None:
    path = FIXTURES / f"{name}.json"
    if os.environ.get("PROMPT_SNAPSHOTS_WRITE"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(prompts, indent=2, ensure_ascii=False) + "\n")
        pytest.skip(f"wrote {path.name}")
    saved = json.loads(path.read_text())
    assert sorted(prompts) == sorted(saved)
    for key, text in prompts.items():
        assert text == saved[key], key


# Team configurations the media team does not have, each a change to its `Team`.
TEAM_VARIANTS = {
    "no_issue_types": lambda team: {"issue_types": ()},
    "no_fallback": lambda team: {"needinfo_fallback": ()},
    "fallback_only": lambda team: {"needinfo_experts": ()},
    "no_experts": lambda team: {"needinfo_experts": (), "needinfo_fallback": ()},
    "no_when": lambda team: {
        "needinfo_experts": tuple(
            e.model_copy(update={"when": None}) for e in team.needinfo_experts
        )
    },
}


def team_sections(system: str) -> str:
    """The system prompt's parts that the team's experts and issue types change."""
    experts = system.split("# Needinfo experts", 1)[1].split("# Bugzilla MCP tools", 1)
    plan = system.split('"duplicate_of": null,', 1)[1].split("```", 1)[0]
    return experts[0] + plan


def test_the_team_prompts_match_the_saved_copy(new_state, tmp_path):
    _check("team", _prompts(new_state, tmp_path))


def test_the_no_team_prompts_match_the_saved_copy(no_team, new_state, tmp_path):
    _check("no_team", _prompts(new_state, tmp_path))


def test_the_team_variants_match_the_saved_copy(monkeypatch):
    team = agent.TEAM
    sections = {}
    for name, update in TEAM_VARIANTS.items():
        monkeypatch.setattr(agent, "TEAM", team.model_copy(update=update(team)))
        sections[name] = team_sections(agent.load_system_prompt())
    _check("team_variants", sections)
