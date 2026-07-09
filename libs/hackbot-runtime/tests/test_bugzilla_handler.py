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


async def test_update_bug_handler_merges_changes_and_comment(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bugzilla_handler, "_request", lambda m, p, b: calls.append((m, p, b))
    )
    changes = {"status": "RESOLVED"}
    await bugzilla_handler.UpdateBugHandler().apply(
        {
            "bug_id": 7,
            "changes": changes,
            "comment": {"body": "done", "is_private": False},
        },
        _ctx(),
    )
    assert calls == [
        (
            "PUT",
            "bug/7",
            {"status": "RESOLVED", "comment": {"body": "done", "is_private": False}},
        )
    ]
    # The caller's `changes` dict must not be mutated by folding in the comment.
    assert changes == {"status": "RESOLVED"}


async def test_update_bug_handler_comment_only(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bugzilla_handler, "_request", lambda m, p, b: calls.append((m, p, b))
    )
    await bugzilla_handler.UpdateBugHandler().apply(
        {"bug_id": 7, "changes": {}, "comment": {"body": "hi", "is_private": True}},
        _ctx(),
    )
    assert calls == [("PUT", "bug/7", {"comment": {"body": "hi", "is_private": True}})]


def test_plan_coalesced_groups_update_plus_comment():
    actions = [
        ("bugzilla.update_bug", {"bug_id": 5, "changes": {"status": "RESOLVED"}}),
        ("bugzilla.add_comment", {"bug_id": 5, "text": "done"}),
    ]
    assert bugzilla_handler.plan_coalesced_groups(actions) == [[0, 1]]


def test_plan_coalesced_groups_closest_comment_wins():
    # update@0 is nearer comment@1 than comment@3; comment@3 stays standalone.
    actions = [
        ("bugzilla.update_bug", {"bug_id": 5, "changes": {}}),
        ("bugzilla.add_comment", {"bug_id": 5, "text": "near"}),
        ("bugzilla.add_comment", {"bug_id": 9, "text": "other bug"}),
        ("bugzilla.add_comment", {"bug_id": 5, "text": "far"}),
    ]
    assert bugzilla_handler.plan_coalesced_groups(actions) == [[0, 1]]


def test_plan_coalesced_groups_multiple_updates_merge_without_comment():
    actions = [
        ("bugzilla.update_bug", {"bug_id": 5, "changes": {"a": 1}}),
        ("bugzilla.update_bug", {"bug_id": 5, "changes": {"b": 2}}),
    ]
    assert bugzilla_handler.plan_coalesced_groups(actions) == [[0, 1]]


def test_plan_coalesced_groups_ignores_unmergeable_and_lonely():
    actions = [
        ("bugzilla.add_comment", {"bug_id": 5, "text": "lone comment"}),
        ("bugzilla.update_bug", {"bug_id": 6, "changes": {}}),  # lone update
        ("bugzilla.add_attachment", {"bug_id": 6}),  # different endpoint
        ("bugzilla.create_bug", {"summary": "x"}),  # POST, no bug_id
        ("bugzilla.update_bug", {"changes": {}}),  # missing bug_id
    ]
    assert bugzilla_handler.plan_coalesced_groups(actions) == []


def test_merge_resolved_combines_changes_and_single_comment():
    entries = [
        ("bugzilla.update_bug", {"bug_id": 5, "changes": {"a": 1}}),
        ("bugzilla.update_bug", {"bug_id": 5, "changes": {"a": 2, "b": 3}}),
        ("bugzilla.add_comment", {"bug_id": 5, "text": "hi", "is_private": True}),
    ]
    assert bugzilla_handler.merge_resolved(entries) == {
        "bug_id": 5,
        "changes": {"a": 2, "b": 3},  # later update wins on conflict
        "comment": {"body": "hi", "is_private": True},
    }


def test_merge_resolved_changes_only():
    entries = [("bugzilla.update_bug", {"bug_id": 5, "changes": {"a": 1}})]
    assert bugzilla_handler.merge_resolved(entries) == {
        "bug_id": 5,
        "changes": {"a": 1},
    }
