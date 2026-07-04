"""Tests for the apply-side Bugzilla action handlers.

Mocks the Bugzilla REST call each handler performs (`_request`) so these
exercise the handlers' own logic — request construction, result parsing,
error handling — without touching a network.
"""

import base64

from hackbot_runtime.actions.handlers import ApplyContext, bugzilla_handler


def _ctx(attachments=None, artifacts=None):
    artifacts = artifacts or {}

    async def download(key):
        return artifacts[key]

    return ApplyContext(
        run_id="run-1", download_artifact=download, attachments=attachments or []
    )


async def test_update_bug_handler_success(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bugzilla_handler,
        "_request",
        lambda m, p, b: calls.append((m, p, b)) or {"bugs": [{"id": 1}]},
    )
    result = await bugzilla_handler.UpdateBugHandler().apply(
        {"bug_id": 1, "changes": {"status": "RESOLVED"}}, _ctx()
    )
    assert result.status == "applied"
    assert result.result["bug_id"] == 1
    assert calls == [("PUT", "bug/1", {"status": "RESOLVED"})]


async def test_update_bug_handler_failure(monkeypatch):
    def _raise(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(bugzilla_handler, "_request", _raise)
    result = await bugzilla_handler.UpdateBugHandler().apply(
        {"bug_id": 1, "changes": {}}, _ctx()
    )
    assert result.status == "failed"
    assert "boom" in result.error


async def test_add_comment_handler_builds_comment_body(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bugzilla_handler, "_request", lambda m, p, b: calls.append((m, p, b))
    )
    await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": "hi", "is_private": True}, _ctx()
    )
    assert calls == [("PUT", "bug/5", {"comment": {"body": "hi", "is_private": True}})]


async def test_add_attachment_handler_downloads_and_base64_encodes(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        bugzilla_handler,
        "_request",
        lambda m, p, b: (seen.update(b), {"ids": [99]})[1],
    )
    ctx = _ctx(
        attachments=[{"name": "file", "uploaded_key": "attachments/0/file"}],
        artifacts={"attachments/0/file": b"diff content"},
    )
    result = await bugzilla_handler.AddAttachmentHandler().apply(
        {
            "bug_id": 5,
            "file_name": "fix.patch",
            "summary": "fix",
            "content_type": "text/plain",
            "is_patch": True,
        },
        ctx,
    )
    assert result.status == "applied"
    assert result.result["attachment_id"] == 99
    assert base64.b64decode(seen["data"]) == b"diff content"


async def test_add_attachment_handler_missing_attachment():
    result = await bugzilla_handler.AddAttachmentHandler().apply(
        {"bug_id": 5, "file_name": "x", "summary": "x", "content_type": "text/plain"},
        _ctx(),
    )
    assert result.status == "failed"


async def test_create_bug_handler_success(monkeypatch):
    monkeypatch.setattr(bugzilla_handler, "_request", lambda m, p, b: {"id": 42})
    result = await bugzilla_handler.CreateBugHandler().apply(
        {"product": "Core", "component": "General", "summary": "s"}, _ctx()
    )
    assert result.status == "applied"
    assert result.result["bug_id"] == 42
