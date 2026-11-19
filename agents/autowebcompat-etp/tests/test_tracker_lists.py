import asyncio
import json
import subprocess
from unittest import mock

import pytest
from hackbot_agents.autowebcompat_etp import tracker_lists


def blocklist(domains):
    return {
        "license": "",
        "categories": {
            "Analytics": [{"Block": {"https://block.xyz/": domains}}],
            "Social": [
                {"Other": {"https://other.org/": ["other.org"], "performance": "true"}}
            ],
        },
    }


ENTITIES = {
    "entities": {
        "Block": {"properties": ["square.com"], "resources": ["editmysite.com"]},
        "Other": {"properties": ["other.org"], "resources": ["other.org"]},
    }
}


def commit(repo, domains, message):
    (repo / "disconnect-blacklist.json").write_text(
        json.dumps(blocklist(domains), indent=2)
    )
    (repo / "social-tracking-protection-blacklist.json").write_text(
        json.dumps(blocklist([]), indent=2)
    )
    (repo / "disconnect-entitylist.json").write_text(json.dumps(ENTITIES, indent=2))
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", message], check=True)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo where cdn3 is added to the blocklist and then removed again."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for key, value in {
        "user.name": "t",
        "user.email": "t@t",
        "commit.gpgsign": "false",
    }.items():
        subprocess.run(["git", "-C", str(tmp_path), "config", key, value], check=True)
    commit(tmp_path, ["ec.editmysite.com"], "Initial lists")
    commit(tmp_path, ["cdn3.editmysite.com", "ec.editmysite.com"], "Add cdn3")
    commit(tmp_path, ["ec.editmysite.com"], "Remove cdn3")
    monkeypatch.setattr(tracker_lists, "_clone", tmp_path)
    monkeypatch.setattr(tracker_lists, "_changes", None)
    return tmp_path


def test_blocklist_entries_match_subdomains():
    lists = {"disconnect-blacklist.json": blocklist(["ec.editmysite.com"])}
    assert tracker_lists.blocklist_entries(lists, "editmysite.com") == [
        {
            "list": "disconnect-blacklist.json",
            "category": "Analytics",
            "org": "Block",
            "domain": "ec.editmysite.com",
        }
    ]
    # Non-list values such as "performance" flags are skipped.
    assert tracker_lists.blocklist_entries(lists, "cdn3.editmysite.com") == []


def test_entity_entries():
    assert [
        e["org"] for e in tracker_lists.entity_entries(ENTITIES, "cdn3.editmysite.com")
    ] == ["Block"]


def test_history_finds_addition_and_removal(repo):
    changes = tracker_lists.history(repo, "cdn3.editmysite.com")

    assert [(c["subject"], c["file"], c["added"], c["removed"]) for c in changes] == [
        ("Remove cdn3", "disconnect-blacklist.json", [], ["cdn3.editmysite.com"]),
        ("Add cdn3", "disconnect-blacklist.json", ["cdn3.editmysite.com"], []),
    ]


def test_added_by_finds_latest_addition(repo):
    # Added, removed and added again: the re-addition is the current entry's.
    commit(repo, ["cdn3.editmysite.com", "ec.editmysite.com"], "Re-add cdn3 (#12)")

    added = tracker_lists.added_by(
        repo, "disconnect-blacklist.json", "cdn3.editmysite.com"
    )

    assert added["subject"] == "Re-add cdn3 (#12)"
    assert added["url"] == f"{tracker_lists.WEB_URL}/commit/{added['commit']}"
    assert added["pull_request"] == f"{tracker_lists.WEB_URL}/pull/12"
    # Commits that only touch other domains don't count.
    initial = tracker_lists.added_by(
        repo, "disconnect-blacklist.json", "ec.editmysite.com"
    )
    assert initial["subject"] == "Initial lists"
    assert "pull_request" not in initial


def test_tool_reports_entries_and_history(repo):
    reply = asyncio.run(
        tracker_lists.check_tracker_lists.handler(
            {"hosts": ["https://cdn3.editmysite.com/x.js"]}
        )
    )
    result = json.loads(reply["content"][0]["text"])
    host = result["hosts"]["cdn3.editmysite.com"]

    assert host["blocklist_entries"] == []
    [entity] = host["entities"]
    assert entity["org"] == "Block"
    assert entity["added_by"]["editmysite.com"]["subject"] == "Initial lists"
    assert [c["subject"] for c in host["recent_changes"]] == ["Remove cdn3", "Add cdn3"]
    assert all(
        c["url"].startswith(tracker_lists.WEB_URL) for c in host["recent_changes"]
    )


def test_tool_needs_hosts():
    reply = asyncio.run(tracker_lists.check_tracker_lists.handler({"hosts": []}))
    assert reply["is_error"]


def test_tool_reports_clone_failure(monkeypatch):
    monkeypatch.setattr(tracker_lists, "_clone", None)
    with mock.patch.object(
        tracker_lists.subprocess,
        "run",
        side_effect=subprocess.CalledProcessError(128, "git clone"),
    ):
        reply = asyncio.run(
            tracker_lists.check_tracker_lists.handler({"hosts": ["a.com"]})
        )
    assert reply["is_error"]
    assert "Could not check the tracker lists" in reply["content"][0]["text"]
