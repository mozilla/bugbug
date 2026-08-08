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


def _fake_request(comments=None, put_result=None):
    """A ``_request`` stand-in that also answers the comment read-back.

    Posting a comment costs two calls now — the PUT, then a GET to recover the
    comment id Bugzilla's update response omits — so any test involving a
    comment has to serve both. Returns ``(request, calls)``.
    """
    calls = []

    def request(method, path, json_body=None):
        calls.append((method, path, json_body))
        if method == "GET":
            bug_id = path.split("/")[1]
            return {"bugs": {bug_id: {"comments": comments or []}}}
        return put_result if put_result is not None else {}

    return request, calls


def _puts(calls):
    return [c for c in calls if c[0] == "PUT"]


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
    request, calls = _fake_request()
    monkeypatch.setattr(bugzilla_handler, "_request", request)
    await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": "hi", "is_private": True}, _ctx()
    )
    # is_markdown is always set: agents author Markdown (permalinks, the italic
    # footer), and without the flag Bugzilla renders the markup literally.
    assert _puts(calls) == [
        (
            "PUT",
            "bug/5",
            {"comment": {"body": "hi", "is_private": True, "is_markdown": True}},
        )
    ]


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
    request, calls = _fake_request()
    monkeypatch.setattr(bugzilla_handler, "_request", request)
    changes = {"status": "RESOLVED"}
    await bugzilla_handler.UpdateBugHandler().apply(
        {
            "bug_id": 7,
            "changes": changes,
            "comment": {"body": "done", "is_private": False},
        },
        _ctx(),
    )
    assert _puts(calls) == [
        (
            "PUT",
            "bug/7",
            {"status": "RESOLVED", "comment": {"body": "done", "is_private": False}},
        )
    ]
    # The caller's `changes` dict must not be mutated by folding in the comment.
    assert changes == {"status": "RESOLVED"}


async def test_update_bug_handler_comment_only(monkeypatch):
    request, calls = _fake_request()
    monkeypatch.setattr(bugzilla_handler, "_request", request)
    await bugzilla_handler.UpdateBugHandler().apply(
        {"bug_id": 7, "changes": {}, "comment": {"body": "hi", "is_private": True}},
        _ctx(),
    )
    assert _puts(calls) == [
        ("PUT", "bug/7", {"comment": {"body": "hi", "is_private": True}})
    ]


# ---- comment id read-back ---------------------------------------------------
#
# Bugzilla's PUT /bug/{id} response carries no comment id, so the handlers read
# the bug's comments back and match on the text they posted. Downstream tools
# use that id to attribute a comment to the agent that wrote it.


async def test_add_comment_handler_records_comment_id(monkeypatch):
    request, calls = _fake_request(
        comments=[{"id": 11, "text": "something else"}, {"id": 12, "text": "hi"}]
    )
    monkeypatch.setattr(bugzilla_handler, "_request", request)
    result = await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": "hi"}, _ctx()
    )
    assert result.status == "applied"
    assert result.result["comment_id"] == 12
    assert ("GET", "bug/5/comment", None) in calls


async def test_add_comment_handler_tolerates_whitespace_round_trip(monkeypatch):
    # Bugzilla may hand back CRLF line endings and trimmed trailing spaces; that
    # must still count as the comment we just posted.
    request, _ = _fake_request(comments=[{"id": 20, "text": "line one\r\nline two"}])
    monkeypatch.setattr(bugzilla_handler, "_request", request)
    result = await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": "line one  \nline two\n"}, _ctx()
    )
    assert result.result["comment_id"] == 20


async def test_add_comment_handler_picks_newest_duplicate(monkeypatch):
    # A re-run can post identical text twice; the id we want is the one this
    # call created, which is the highest.
    request, _ = _fake_request(
        comments=[{"id": 30, "text": "same"}, {"id": 31, "text": "same"}]
    )
    monkeypatch.setattr(bugzilla_handler, "_request", request)
    result = await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": "same"}, _ctx()
    )
    assert result.result["comment_id"] == 31


async def test_add_comment_handler_applied_when_no_text_matches(monkeypatch):
    # No confident match: report no id rather than guess at the newest comment,
    # which could be an engineer replying in the same moment.
    request, _ = _fake_request(comments=[{"id": 40, "text": "unrelated"}])
    monkeypatch.setattr(bugzilla_handler, "_request", request)
    result = await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": "hi"}, _ctx()
    )
    assert result.status == "applied"
    assert "comment_id" not in result.result


async def test_add_comment_handler_applied_when_read_back_fails(monkeypatch):
    # The comment is already posted by then, so a failed read-back must not turn
    # a successful post into a failed action.
    def request(method, path, json_body=None):
        if method == "GET":
            raise RuntimeError("bugzilla down")
        return {}

    monkeypatch.setattr(bugzilla_handler, "_request", request)
    result = await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": "hi"}, _ctx()
    )
    assert result.status == "applied"
    assert result.result["bug_id"] == 5
    assert "comment_id" not in result.result


async def test_update_bug_handler_records_comment_id_for_folded_comment(monkeypatch):
    request, calls = _fake_request(comments=[{"id": 50, "text": "done"}])
    monkeypatch.setattr(bugzilla_handler, "_request", request)
    result = await bugzilla_handler.UpdateBugHandler().apply(
        {
            "bug_id": 7,
            "changes": {"status": "RESOLVED"},
            "comment": {"body": "done", "is_private": False},
        },
        _ctx(),
    )
    assert result.result["comment_id"] == 50
    assert ("GET", "bug/7/comment", None) in calls


async def test_update_bug_handler_changes_only_does_not_read_back(monkeypatch):
    # A changes-only update created no comment, so there is nothing to look up
    # and no reason to spend a request on it.
    request, calls = _fake_request()
    monkeypatch.setattr(bugzilla_handler, "_request", request)
    result = await bugzilla_handler.UpdateBugHandler().apply(
        {"bug_id": 7, "changes": {"status": "RESOLVED"}}, _ctx()
    )
    assert "comment_id" not in result.result
    assert [c[0] for c in calls] == ["PUT"]


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
        "comment": {"body": "hi", "is_private": True, "is_markdown": True},
    }


def test_merge_resolved_changes_only():
    entries = [("bugzilla.update_bug", {"bug_id": 5, "changes": {"a": 1}})]
    assert bugzilla_handler.merge_resolved(entries) == {
        "bug_id": 5,
        "changes": {"a": 1},
    }
