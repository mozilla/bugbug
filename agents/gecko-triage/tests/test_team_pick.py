"""Tests for picking the team from the bug's component when the run input names none."""

import os
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager

from hackbot_agents.gecko_triage import __main__ as entry
from hackbot_agents.gecko_triage import team_pick
from hackbot_agents.gecko_triage.teamconfig import TEAMS_DIR


def _broker(reply=None, error=None):
    calls = []

    @asynccontextmanager
    async def session(url, timeout):
        async def call(tool, args):
            calls.append((url, tool, args))
            if error:
                raise error
            return reply

        yield call

    return session, calls


def test_a_media_component_picks_the_media_team():
    assert team_pick.team_for("Core", "Audio/Video: Playback") == "media"


def test_a_component_of_no_team_picks_none():
    assert team_pick.team_for("Core", "DOM: Core & HTML") is None
    assert team_pick.team_for(None, None) is None


def test_a_team_directory_with_no_rules_is_left_out(tmp_path):
    shutil.copytree(TEAMS_DIR / "media", tmp_path / "media")
    (tmp_path / "media" / "rules" / "classify.md").unlink()
    assert team_pick.team_for("Core", "Audio/Video: Playback", tmp_path) is None


def test_two_teams_with_the_component_pick_none(tmp_path):
    for name in ("media", "copy"):
        shutil.copytree(TEAMS_DIR / "media", tmp_path / name)
    assert team_pick.team_for("Core", "Audio/Video: Playback", tmp_path) is None


async def test_the_pick_reads_the_bug_through_the_broker(monkeypatch):
    session, calls = _broker(
        {"bugs": [{"product": "Core", "component": "Audio/Video: Playback"}]}
    )
    monkeypatch.setattr(team_pick, "broker_session", session)
    assert await team_pick.pick_team("http://127.0.0.1:8765/", 9000001) == "media"
    assert calls == [
        (
            "http://127.0.0.1:8765/mcp",
            "get_bugs",
            {"ids": [9000001], "include_fields": ["product", "component"]},
        )
    ]


async def test_an_unreadable_bug_picks_none(monkeypatch):
    session, _ = _broker(error=RuntimeError("bug 9000001 is not public"))
    monkeypatch.setattr(team_pick, "broker_session", session)
    assert await team_pick.pick_team("http://broker", 9000001) is None
    session, _ = _broker({"bugs": [], "inaccessible": [9000001]})
    monkeypatch.setattr(team_pick, "broker_session", session)
    assert await team_pick.pick_team("http://broker", 9000001) is None


def test_a_team_input_wins_over_the_pick(monkeypatch):
    monkeypatch.setenv("TEAM", "media")

    async def never(url, bug):
        raise AssertionError("no pick when the input names a team")

    monkeypatch.setattr(entry, "pick_team", never)
    entry.set_team()
    assert os.environ["TEAM"] == "media"


def test_no_team_input_sets_the_picked_team(monkeypatch):
    monkeypatch.delenv("TEAM", raising=False)
    monkeypatch.setenv("BROKER_URL", "http://broker")
    monkeypatch.setenv("BUG_ID", "9000001")
    picked = []

    async def pick(url, bug):
        picked.append((url, bug))
        return "media"

    monkeypatch.setattr(entry, "pick_team", pick)
    entry.set_team()
    assert picked == [("http://broker", 9000001)]
    assert os.environ["TEAM"] == "media"


def test_no_picked_team_leaves_the_run_with_no_team(monkeypatch):
    monkeypatch.setenv("TEAM", "")
    monkeypatch.setenv("BROKER_URL", "http://broker")
    monkeypatch.setenv("BUG_ID", "9000001")

    async def pick(url, bug):
        return None

    monkeypatch.setattr(entry, "pick_team", pick)
    entry.set_team()
    assert os.environ["TEAM"] == ""


def test_the_entry_point_picks_the_team_before_config_reads_it():
    # `config` reads `TEAM` at import, so starting a run must not import it.
    code = (
        "import sys, hackbot_agents.gecko_triage.__main__; "
        "print('hackbot_agents.gecko_triage.config' in sys.modules)"
    )
    env = {k: v for k, v in os.environ.items() if k != "TEAM"}
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "False"
