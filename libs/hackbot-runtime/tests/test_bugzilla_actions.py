"""Tests for the bugzilla action handlers (footers, mime, merge, errors)."""

import pytest
from hackbot_runtime.actions import ActionInputError, ActionsRecorder, bugzilla


async def test_add_comment_appends_footer():
    rec = ActionsRecorder()
    await bugzilla.add_comment(rec, bug_id=1, text="Looks invalid.", reasoning="r")
    text = rec.actions[0]["params"]["text"]
    assert text.startswith("Looks invalid.")
    assert "automated analysis result" in text
    assert rec.actions[0]["params"]["is_private"] is False


async def test_add_attachment_patch_forces_text_plain(tmp_path):
    src = tmp_path / "fix.patch"
    src.write_text("diff")
    rec = ActionsRecorder(artifacts_dir=tmp_path / "a")
    await bugzilla.add_attachment(
        rec, bug_id=1, file_path=str(src), reasoning="r", is_patch=True, comment="fix"
    )
    params = rec.actions[0]["params"]
    assert params["content_type"] == "text/plain"
    assert params["is_patch"] is True
    assert "suggested fix" in params["comment"]
    assert params["file_name"] == "fix.patch"


async def test_add_attachment_guesses_mime(tmp_path):
    src = tmp_path / "note.txt"
    src.write_text("hi")
    rec = ActionsRecorder(artifacts_dir=tmp_path / "a")
    await bugzilla.add_attachment(rec, bug_id=1, file_path=str(src), reasoning="r")
    assert rec.actions[0]["params"]["content_type"] == "text/plain"


async def test_add_attachment_missing_file_raises():
    rec = ActionsRecorder()
    with pytest.raises(ActionInputError):
        await bugzilla.add_attachment(
            rec, bug_id=1, file_path="/no/such.patch", reasoning="r"
        )
    assert rec.actions == []


async def test_create_bug_merges_extra_top_level_wins():
    rec = ActionsRecorder()
    await bugzilla.create_bug(
        rec,
        product="Core",
        component="JS",
        summary="x",
        version="unspecified",
        description="desc",
        reasoning="r",
        extra={"severity": "S3", "product": "IGNORED"},
    )
    body = rec.actions[0]["params"]
    assert body["severity"] == "S3"
    assert body["product"] == "Core"  # explicit arg wins over extra
    assert body["is_markdown"] is True


async def test_handlers_return_confirmation_string():
    rec = ActionsRecorder()
    msg = await bugzilla.update_bug(
        rec, bug_id=1, changes={"severity": "S2"}, reasoning="r"
    )
    assert isinstance(msg, str)
    assert "bugzilla.update_bug" in msg
