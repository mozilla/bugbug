"""Tests for ActionsRecorder: attachment upload vs local-copy behavior, hooks."""

from pathlib import Path

import pytest
from agent_tools.registry import ToolError
from hackbot_runtime.actions import ActionsRecorder


class _StubUploader:
    def __init__(self):
        self.uploaded: list[tuple[str, Path]] = []

    def upload_file(self, name, path, content_type=None):
        self.uploaded.append((name, Path(path)))


def test_record_basic_shape():
    rec = ActionsRecorder()
    returned = rec.record(
        "bugzilla.update_bug",
        {"bug_id": 1, "changes": {"severity": "S2"}},
        reasoning="rule X",
    )
    assert returned == rec.actions[0]
    assert rec.actions == [
        {
            "type": "bugzilla.update_bug",
            "params": {"bug_id": 1, "changes": {"severity": "S2"}},
            "reasoning": "rule X",
        }
    ]


def test_action_type_is_positional():
    # The first parameter is `action_type`; passing it positionally must work.
    rec = ActionsRecorder()
    rec.record("bugzilla.add_comment", {"bug_id": 1})
    assert rec.actions[0]["type"] == "bugzilla.add_comment"


def test_attachment_uploaded_when_uploader_set(tmp_path):
    src = tmp_path / "fix.patch"
    src.write_text("diff")
    uploader = _StubUploader()
    artifacts = tmp_path / "artifacts"
    rec = ActionsRecorder(uploader=uploader, artifacts_dir=artifacts)

    rec.record("bugzilla.add_attachment", {"bug_id": 1}, attachments={"file": src})

    # Uploaded under the stable key; NOT copied locally.
    assert uploader.uploaded == [("attachments/0/file", src)]
    assert not artifacts.exists()
    assert rec.actions[0]["attachments"] == [
        {"name": "file", "uploaded_key": "attachments/0/file"}
    ]


def test_attachment_copied_when_no_uploader(tmp_path):
    src = tmp_path / "fix.patch"
    src.write_text("diff-content")
    artifacts = tmp_path / "artifacts"
    rec = ActionsRecorder(artifacts_dir=artifacts)

    rec.record("bugzilla.add_attachment", {"bug_id": 1}, attachments={"file": src})

    copied = artifacts / "attachments/0/file"
    assert copied.read_text() == "diff-content"
    assert rec.actions[0]["attachments"] == [
        {"name": "file", "uploaded_key": "attachments/0/file"}
    ]


def test_attachment_key_uses_action_index(tmp_path):
    src = tmp_path / "f.txt"
    src.write_text("x")
    rec = ActionsRecorder(artifacts_dir=tmp_path / "a")
    rec.record("bugzilla.update_bug", {"bug_id": 1})
    rec.record("bugzilla.add_attachment", {"bug_id": 1}, attachments={"file": src})
    assert rec.actions[1]["attachments"][0]["uploaded_key"] == "attachments/1/file"


def test_ref_included_when_given():
    rec = ActionsRecorder()
    rec.record("phabricator.submit_patch", {"bug_id": 1}, ref="patch")
    assert rec.actions[0]["ref"] == "patch"


def test_ref_omitted_when_not_given():
    rec = ActionsRecorder()
    rec.record("bugzilla.update_bug", {"bug_id": 1})
    assert "ref" not in rec.actions[0]


def test_hooks_run_in_order_and_mutations_are_recorded():
    calls: list[str] = []

    def first(action):
        calls.append("first")
        action["params"]["priority"] = "P1"

    def second(action):
        calls.append("second")
        # Sees the previous hook's mutation, and the built action.
        action["params"]["seen"] = action["params"]["priority"]

    rec = ActionsRecorder(hooks={"bugzilla.update_bug": [first, second]})
    returned = rec.record("bugzilla.update_bug", {"bug_id": 1}, reasoning="rule X")

    assert calls == ["first", "second"]
    assert returned["params"] == {"bug_id": 1, "priority": "P1", "seen": "P1"}
    assert rec.actions[0] == returned


def test_hooks_only_run_for_their_action_type():
    seen: list[str] = []
    rec = ActionsRecorder(
        hooks={"phabricator.submit_patch": [lambda action: seen.append(action["type"])]}
    )

    rec.record("bugzilla.add_comment", {"bug_id": 1})
    assert seen == []

    rec.record("phabricator.submit_patch", {"bug_id": 1})
    assert seen == ["phabricator.submit_patch"]


def test_hook_sees_ref_but_runs_before_attachments_are_published(tmp_path):
    src = tmp_path / "fix.patch"
    src.write_text("diff")
    captured: list[dict] = []

    def capture(action):
        captured.append(dict(action))

    rec = ActionsRecorder(
        artifacts_dir=tmp_path / "a",
        hooks={"bugzilla.add_attachment": [capture]},
    )
    recorded = rec.record(
        "bugzilla.add_attachment",
        {"bug_id": 1},
        ref="patch",
        attachments={"file": src},
    )

    assert captured[0]["ref"] == "patch"
    # Publishing happens only once the hooks have accepted the action.
    assert "attachments" not in captured[0]
    assert recorded["attachments"] == [
        {"name": "file", "uploaded_key": "attachments/0/file"}
    ]


def test_raising_hook_aborts_the_recording():
    def reject(action):
        raise ValueError("no")

    def never(action):  # pragma: no cover - must not run
        raise AssertionError("later hook ran after an earlier one raised")

    rec = ActionsRecorder(hooks={"bugzilla.update_bug": [reject, never]})

    with pytest.raises(ValueError, match="no"):
        rec.record("bugzilla.update_bug", {"bug_id": 1})

    assert rec.actions == []


def test_raising_hook_publishes_no_attachment(tmp_path):
    src = tmp_path / "fix.patch"
    src.write_text("diff")
    uploader = _StubUploader()
    artifacts = tmp_path / "artifacts"

    def reject(action):
        raise ValueError("no")

    rec = ActionsRecorder(
        uploader=uploader,
        artifacts_dir=artifacts,
        hooks={"bugzilla.add_attachment": [reject]},
    )

    with pytest.raises(ValueError, match="no"):
        rec.record("bugzilla.add_attachment", {"bug_id": 1}, attachments={"file": src})

    # Nothing uploaded or copied: an aborted recording leaves no orphaned file
    # at a key the next recorded action would reuse.
    assert uploader.uploaded == []
    assert not artifacts.exists()

    # The next successful record still owns attachments/0, with no leftover
    # from the rejected action sitting at that key.
    rec.record("bugzilla.update_bug", {"bug_id": 1})
    assert [a["type"] for a in rec.actions] == ["bugzilla.update_bug"]


def test_add_hook_appends_after_constructor_hooks():
    calls: list[str] = []
    rec = ActionsRecorder(
        hooks={"bugzilla.update_bug": [lambda action: calls.append("ctor")]}
    )
    rec.add_hook("bugzilla.update_bug", lambda action: calls.append("added"))
    rec.add_hook("bugzilla.add_comment", lambda action: calls.append("other-type"))

    rec.record("bugzilla.update_bug", {"bug_id": 1})

    assert calls == ["ctor", "added"]


def test_constructor_hooks_are_copied():
    hooks: dict[str, list] = {"bugzilla.update_bug": []}
    rec = ActionsRecorder(hooks=hooks)
    hooks["bugzilla.update_bug"].append(
        lambda action: pytest.fail("mutating the caller's mapping must not register")
    )

    rec.record("bugzilla.update_bug", {"bug_id": 1})
    assert len(rec.actions) == 1


def test_list_actions_returns_stable_ids_and_complete_detached_payloads():
    rec = ActionsRecorder()
    rec.record(
        "phabricator.submit_patch",
        {"bug_id": 1, "title": "Fix"},
        reasoning="verified fix",
        ref="patch",
    )
    rec.record(
        "bugzilla.add_comment",
        {"bug_id": 1, "text": "See {{actions.patch.url}}"},
        reasoning="announce the patch",
    )

    listed = rec.list_actions()

    assert [action["action_id"] for action in listed] == ["action-0", "action-1"]
    assert listed[0] == {
        "action_id": "action-0",
        "type": "phabricator.submit_patch",
        "params": {"bug_id": 1, "title": "Fix"},
        "reasoning": "verified fix",
        "ref": "patch",
    }
    assert "action_id" not in rec.actions[0]

    listed[0]["params"]["title"] = "mutated copy"
    assert rec.actions[0]["params"]["title"] == "Fix"


def test_remove_action_deletes_only_the_requested_action():
    rec = ActionsRecorder()
    rec.record("bugzilla.update_bug", {"bug_id": 1}, reasoning="first")
    rec.record("bugzilla.add_comment", {"bug_id": 1}, reasoning="second")

    removed = rec.remove_action("action-0")

    assert removed["action_id"] == "action-0"
    assert removed["reasoning"] == "first"
    assert rec.list_actions()[0]["action_id"] == "action-1"
    assert [action["type"] for action in rec.actions] == ["bugzilla.add_comment"]


def test_remove_action_rejects_unknown_or_already_removed_id():
    rec = ActionsRecorder()
    rec.record("bugzilla.update_bug", {"bug_id": 1})
    rec.remove_action("action-0")

    with pytest.raises(ToolError, match="No recorded action"):
        rec.remove_action("action-0")


def test_removed_action_id_and_attachment_key_are_not_reused(tmp_path):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("first")
    second.write_text("second")
    rec = ActionsRecorder(artifacts_dir=tmp_path / "artifacts")

    rec.record("bugzilla.add_attachment", {"bug_id": 1}, attachments={"file": first})
    rec.remove_action("action-0")
    rec.record("bugzilla.add_attachment", {"bug_id": 1}, attachments={"file": second})

    assert rec.list_actions()[0]["action_id"] == "action-1"
    assert rec.actions[0]["attachments"] == [
        {"name": "file", "uploaded_key": "attachments/1/file"}
    ]
    assert (tmp_path / "artifacts" / "attachments" / "0" / "file").read_text() == (
        "first"
    )
    assert (tmp_path / "artifacts" / "attachments" / "1" / "file").read_text() == (
        "second"
    )
