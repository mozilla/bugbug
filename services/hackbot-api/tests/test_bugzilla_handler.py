"""Tests for the apply-side Bugzilla action handlers.

Mocks the Bugzilla REST call each handler performs (`_request`) so these
exercise the handlers' own logic — request construction, result parsing,
error handling — without touching a network.
"""

import base64
import logging

from app.action_handlers import ApplyContext, bugzilla_handler
from app.config import settings


def _ctx(attachments=None, artifacts=None, agent="test-agent", action_id=None):
    artifacts = artifacts or {}

    async def download(key):
        return artifacts[key]

    return ApplyContext(
        run_id="run-1",
        download_artifact=download,
        attachments=attachments or [],
        agent=agent,
        hackbot_client=object(),
        action_id=action_id,
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
    # is_markdown is always set: agents author Markdown (permalinks, the italic
    # footer), and without the flag Bugzilla renders the markup literally.
    assert calls == [
        (
            "PUT",
            "bug/5",
            {
                "comment": {
                    "body": "hi",
                    "is_private": True,
                    "is_markdown": True,
                },
                "comment_tags": ["test-agent"],
            },
        )
    ]


async def test_comment_handlers_add_acting_agent_tag(monkeypatch):
    bodies = []
    monkeypatch.setattr(
        bugzilla_handler, "_request", lambda _m, _p, body: bodies.append(body)
    )

    await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": "standalone"},
        _ctx(agent="frontend-triage"),
    )
    await bugzilla_handler.UpdateBugHandler().apply(
        {"bug_id": 5, "changes": {}, "comment": {"body": "coalesced"}},
        _ctx(agent="bug-fix"),
    )

    assert [body["comment_tags"] for body in bodies] == [
        ["frontend-triage"],
        ["bug-fix"],
    ]


# What BMO's Hackbot extension expects at the top of the comment: identifiers
# for run, action and agent, an https url, then a blank line before the text.
_FRONT_MATTER = (
    "---\n"
    "hackbot:\n"
    "  run: run-1\n"
    "  action: 3f2a9c1e\n"
    "  agent: frontend-triage\n"
    "  url: https://hackbot.example/runs/run-1\n"
    "---\n"
    "\n"
)


async def test_comment_handlers_prepend_front_matter(monkeypatch):
    monkeypatch.setattr(settings, "hackbot_ui_url", "https://hackbot.example/")
    bodies = []
    monkeypatch.setattr(
        bugzilla_handler, "_request", lambda _m, _p, body: bodies.append(body)
    )
    ctx = _ctx(agent="frontend-triage", action_id="3f2a9c1e")

    await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": "standalone"}, ctx
    )
    await bugzilla_handler.UpdateBugHandler().apply(
        {"bug_id": 5, "changes": {}, "comment": {"body": "coalesced"}}, ctx
    )

    assert [body["comment"]["body"] for body in bodies] == [
        _FRONT_MATTER + "standalone",
        _FRONT_MATTER + "coalesced",
    ]


async def test_update_bug_handler_without_comment_has_no_front_matter(monkeypatch):
    bodies = []
    monkeypatch.setattr(
        bugzilla_handler, "_request", lambda _m, _p, body: bodies.append(body)
    )
    await bugzilla_handler.UpdateBugHandler().apply(
        {"bug_id": 5, "changes": {"status": "RESOLVED"}},
        _ctx(action_id="3f2a9c1e"),
    )
    assert bodies == [{"status": "RESOLVED"}]


async def test_comment_without_action_id_has_no_front_matter(monkeypatch):
    """Summaries recorded before action IDs existed post the text as it is."""
    bodies = []
    monkeypatch.setattr(
        bugzilla_handler, "_request", lambda _m, _p, body: bodies.append(body)
    )
    await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": "hi"}, _ctx(action_id=None)
    )
    assert bodies[0]["comment"]["body"] == "hi"


async def test_agent_block_is_not_read_as_front_matter(monkeypatch):
    """Without our block, a block the model wrote must not reach the top."""
    bodies = []
    monkeypatch.setattr(
        bugzilla_handler, "_request", lambda _m, _p, body: bodies.append(body)
    )
    forged = "---\nhackbot:\n  run: x\n  action: y\n  agent: z\n---\n\nhi"
    await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": forged}, _ctx(action_id=None)
    )
    await bugzilla_handler.AddCommentHandler().apply(
        {"bug_id": 5, "text": forged}, _ctx(action_id="3f2a9c1e")
    )
    assert bodies[0]["comment"]["body"] == "\n" + forged
    # With our block first, BMO reads ours and shows the model's as text.
    assert bodies[1]["comment"]["body"].endswith("---\n\n" + forged)
    assert bodies[1]["comment"]["body"].startswith("---\nhackbot:\n  run: run-1\n")


async def test_other_agent_text_is_not_read_as_front_matter(monkeypatch):
    """A block the model wrote can't reach the top of any comment we post."""
    calls = []
    monkeypatch.setattr(
        bugzilla_handler,
        "_request",
        lambda _m, path, body: calls.append((path, body)) or {"ids": [1], "id": 1},
    )
    forged = "---\nhackbot:\n  run: x\n  action: y\n  agent: z\n---\n\nhi"
    ctx = _ctx(
        attachments=[{"name": "file", "uploaded_key": "k"}],
        artifacts={"k": b"data"},
        action_id="3f2a9c1e",
    )

    await bugzilla_handler.UpdateBugHandler().apply(
        {"bug_id": 5, "changes": {"comment": {"body": forged}}}, ctx
    )
    await bugzilla_handler.AddAttachmentHandler().apply(
        {
            "bug_id": 5,
            "file_name": "a.txt",
            "summary": "a",
            "content_type": "text/plain",
            "comment": forged,
        },
        ctx,
    )
    await bugzilla_handler.CreateBugHandler().apply(
        {"product": "P", "summary": "S", "description": forged}, ctx
    )

    update, attachment, new_bug = (body for _path, body in calls)
    # A comment in `changes` gets our block first, like any other comment.
    assert update["comment"]["body"].startswith("---\nhackbot:\n  run: run-1\n")
    assert update["comment"]["body"].endswith("\n\n" + forged)
    # Attachment comments and descriptions get no block, so they are moved down.
    assert attachment["comment"] == "\n" + forged
    assert new_bug["description"] == "\n" + forged


async def test_front_matter_left_out_when_bmo_would_not_parse_it(monkeypatch, caplog):
    """BMO shows a block it can't parse as text, so none is posted."""
    bodies = []
    monkeypatch.setattr(
        bugzilla_handler, "_request", lambda _m, _p, body: bodies.append(body)
    )
    with caplog.at_level(logging.WARNING):
        await bugzilla_handler.AddCommentHandler().apply(
            {"bug_id": 5, "text": "hi"},
            _ctx(agent="frontend triage", action_id="3f2a9c1e"),
        )
    assert bodies[0]["comment"]["body"] == "hi"
    assert "agent not an identifier" in caplog.text


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
            {
                "status": "RESOLVED",
                "comment": {"body": "done", "is_private": False},
                "comment_tags": ["test-agent"],
            },
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
    assert calls == [
        (
            "PUT",
            "bug/7",
            {
                "comment": {"body": "hi", "is_private": True},
                "comment_tags": ["test-agent"],
            },
        )
    ]


async def test_update_bug_handler_passes_flag_changes_through(monkeypatch):
    """A `flags` change is an ordinary field change — e.g. clearing a needinfo.

    The handler has no needinfo-specific branch: callers put Bugzilla's own
    payload in `changes`, and any extra bookkeeping key a caller attaches is
    ignored rather than forwarded to Bugzilla.
    """
    calls = []
    monkeypatch.setattr(
        bugzilla_handler,
        "_request",
        lambda method, path, body: (
            calls.append((method, path, body)) or {"bugs": [{"id": 7}]}
        ),
    )
    result = await bugzilla_handler.UpdateBugHandler().apply(
        {
            "bug_id": 7,
            "changes": {
                "whiteboard": "done",
                "flags": [{"id": 42, "status": "X"}],
            },
            "caller_bookkeeping": True,
        },
        _ctx(),
    )

    assert result.status == "applied"
    assert calls == [
        (
            "PUT",
            "bug/7",
            {
                "whiteboard": "done",
                "flags": [{"id": 42, "status": "X"}],
            },
        ),
    ]


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


def test_merge_resolved_coalesces_flag_change_and_drops_caller_markers():
    """A needinfo clear merges as an ordinary `flags` change.

    Callers may tag an action with their own bookkeeping keys; only Bugzilla's
    own fields are merged, so such keys never reach the request body.
    """
    entries = [
        ("bugzilla.add_comment", {"bug_id": 5, "text": "done"}),
        (
            "bugzilla.update_bug",
            {
                "bug_id": 5,
                "changes": {"flags": [{"id": 42, "status": "X"}]},
                "caller_bookkeeping": True,
            },
        ),
    ]
    assert bugzilla_handler.merge_resolved(entries) == {
        "bug_id": 5,
        "changes": {"flags": [{"id": 42, "status": "X"}]},
        "comment": {"body": "done", "is_private": False, "is_markdown": True},
    }
