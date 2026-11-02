"""Tests for the reads of a run's recorded actions."""

import ast
from pathlib import Path

from hackbot_agents.gecko_triage.actions_view import (
    Update,
    action_params,
    comment_texts,
    is_bug_id,
    needinfo_requestees,
    updates,
)

PACKAGE = Path(__file__).parents[1] / "hackbot_agents" / "gecko_triage"
BUG = 9000001
DUPE = 9000002


def _update(action_id, bug_id, changes):
    return {
        "action_id": action_id,
        "type": "bugzilla.update_bug",
        "params": {"bug_id": bug_id, "changes": changes},
    }


ACTIONS = [
    {"action_id": "c", "type": "bugzilla.add_comment", "params": {"text": "Hello"}},
    _update("u1", BUG, {"severity": "S3"}),
    _update("u2", DUPE, {"blocks": {"add": [1]}}),
    _update("u3", BUG, "junk"),
    {"type": "bugzilla.update_bug", "params": "junk"},
]


def test_updates_are_all_bug_updates_in_order():
    assert updates(ACTIONS) == [
        Update("u1", BUG, {"severity": "S3"}),
        Update("u2", DUPE, {"blocks": {"add": [1]}}),
        Update("u3", BUG, {}),
        Update("", None, {}),
    ]


def test_updates_on_one_bug():
    assert [u.action_id for u in updates(ACTIONS, BUG)] == ["u1", "u3"]
    assert [u.action_id for u in updates(ACTIONS, DUPE)] == ["u2"]
    assert updates(ACTIONS, 1) == []


def test_comment_texts_keep_only_text():
    actions = [
        *ACTIONS,
        {"type": "bugzilla.add_comment", "params": {"text": 7}},
        {"type": "bugzilla.add_comment"},
    ]
    assert comment_texts(actions) == ["Hello"]


def test_needinfo_requestees():
    changes = {
        "flags": [
            {"name": "needinfo", "status": "?", "requestee": "a@example.com"},
            {"name": "needinfo", "status": "?"},
            "junk",
        ]
    }
    assert needinfo_requestees(changes) == ["a@example.com", ""]
    assert needinfo_requestees({}) == []
    assert needinfo_requestees({"flags": None}) == []


def test_action_params_and_bug_ids():
    assert action_params({"params": {"a": 1}}) == {"a": 1}
    assert action_params({"params": "junk"}) == {} == action_params({})
    assert is_bug_id(1) and is_bug_id(BUG)
    for value in (0, -1, True, "1", None, 1.0):
        assert not is_bug_id(value)


def test_no_module_imports_a_private_name_of_another():
    for path in PACKAGE.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level:
                private = [a.name for a in node.names if a.name.startswith("_")]
                assert not private, f"{path.name} imports {private} from {node.module}"
