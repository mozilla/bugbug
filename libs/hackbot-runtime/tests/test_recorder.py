"""Tests for ActionsRecorder: attachment upload vs local-copy behavior."""

from pathlib import Path

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
