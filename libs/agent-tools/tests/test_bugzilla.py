"""Tests for the Bugzilla read tools."""

from unittest.mock import MagicMock

import bugsy
import pytest
from agent_tools import bugzilla
from agent_tools.bugzilla import BugzillaContext
from agent_tools.claude_sdk import build_sdk_server
from agent_tools.registry import ToolError
from mcp.types import ListToolsRequest


async def _list(server):
    return (
        await server.request_handlers[ListToolsRequest](
            ListToolsRequest(method="tools/list")
        )
    ).root.tools


async def test_exposes_read_only_tools():
    config = build_sdk_server(
        "bugzilla", BugzillaContext(client=MagicMock()), bugzilla.TOOLS
    )
    assert config["type"] == "sdk"
    tools = await _list(config["instance"])
    assert {t.name for t in tools} == {
        "search_bugs",
        "get_bugs",
        "get_bug_comments",
        "get_bug_attachments",
        "download_attachment",
    }


async def test_search_bugs_returns_data():
    client = MagicMock()
    client.request.return_value = {"bugs": [{"id": 1}, {"id": 2}]}
    result = await bugzilla.search_bugs(
        BugzillaContext(client=client), params={"id": "1,2"}
    )
    assert result == {"count": 2, "bugs": [{"id": 1}, {"id": 2}]}


async def test_search_bugs_raises_tool_error_on_bugsy_failure():
    import bugsy

    client = MagicMock()
    err = bugsy.BugsyException("nope")
    err.code = 102
    client.request.side_effect = err
    with pytest.raises(ToolError) as ei:
        await bugzilla.search_bugs(BugzillaContext(client=client), params={})
    assert ei.value.payload["error"] == "access_denied"


def _attachment_client(att, *, data="aGVsbG8="):
    """A bugsy client that serves one attachment, with and without its data.

    Mirrors Bugzilla: `exclude_fields=data` returns the metadata only, and the
    unqualified request returns the same record plus base64 `data`. Records every
    (path, params) pair so a test can assert what was and was not fetched.
    """
    client = MagicMock()
    calls = []

    def request(path, params=None):
        params = params or {}
        calls.append((path, params))
        if path.endswith("/attachment"):
            body = (
                dict(att)
                if params.get("exclude_fields") == "data"
                else {**att, "data": data}
            )
            return {"bugs": {"1": [body]}}
        if params.get("exclude_fields") == "data":
            return {"attachments": {str(att["id"]): dict(att)}}
        return {"attachments": {str(att["id"]): {**att, "data": data}}}

    client.request.side_effect = request
    client.calls = calls
    return client


def _att(**overrides):
    base = {"id": 7, "file_name": "shot.png", "content_type": "image/png"}
    return {**base, **overrides}


async def test_download_attachment_writes_allowed_image(tmp_path):
    client = _attachment_client(_att())
    dest = tmp_path / "shot.png"
    result = await bugzilla.download_attachment(
        BugzillaContext(client=client), attachment_id=7, dest_path=str(dest)
    )
    assert dest.read_bytes() == b"hello"
    assert result["content_type"] == "image/png"
    assert result["size_bytes"] == 5
    # Metadata is probed before the bytes are asked for.
    assert client.calls[0] == ("bug/attachment/7", {"exclude_fields": "data"})
    assert client.calls[1] == ("bug/attachment/7", {})


async def test_download_attachment_writes_allowed_text(tmp_path):
    client = _attachment_client(_att(file_name="update.log", content_type="text/x-log"))
    dest = tmp_path / "update.log"
    await bugzilla.download_attachment(
        BugzillaContext(client=client), attachment_id=7, dest_path=str(dest)
    )
    assert dest.read_bytes() == b"hello"


@pytest.mark.parametrize(
    "att",
    [
        _att(file_name="screencast.mp4", content_type="video/mp4"),
        _att(file_name="logs.zip", content_type="application/zip"),
        _att(file_name="clip.webm", content_type="video/webm"),
        _att(file_name="mystery", content_type=""),
        _att(file_name="mystery", content_type=None),
    ],
    ids=["mp4", "zip", "webm", "empty-type", "no-type"],
)
async def test_download_attachment_refuses_unreadable_types(tmp_path, att):
    client = _attachment_client(att)
    dest = tmp_path / "out.bin"
    with pytest.raises(ToolError) as ei:
        await bugzilla.download_attachment(
            BugzillaContext(client=client), attachment_id=7, dest_path=str(dest)
        )
    assert ei.value.payload["error"] == "attachment_type_not_allowed"
    assert not dest.exists()
    # The refusal costs one metadata request; the bytes are never fetched.
    assert client.calls == [("bug/attachment/7", {"exclude_fields": "data"})]


@pytest.mark.parametrize(
    ("file_name", "allowed"),
    [
        ("update.log", True),
        ("patch.diff", True),
        ("bug.patch", True),
        ("shot.png", True),
        ("screencast.mp4", False),
        ("trace", False),
    ],
)
async def test_octet_stream_resolves_by_file_name(tmp_path, file_name, allowed):
    """Bugzilla's fallback type covers both plain logs and untyped screencasts."""
    client = _attachment_client(
        _att(file_name=file_name, content_type="application/octet-stream")
    )
    dest = tmp_path / "out.bin"
    ctx = BugzillaContext(client=client)
    if allowed:
        await bugzilla.download_attachment(ctx, attachment_id=7, dest_path=str(dest))
        assert dest.read_bytes() == b"hello"
    else:
        with pytest.raises(ToolError) as ei:
            await bugzilla.download_attachment(
                ctx, attachment_id=7, dest_path=str(dest)
            )
        assert ei.value.payload["error"] == "attachment_type_not_allowed"


async def test_is_patch_overrides_an_odd_content_type(tmp_path):
    """A patch is text even when the uploader typed it as something else."""
    client = _attachment_client(
        _att(
            file_name="fix.patch",
            content_type="application/octet-stream",
            is_patch=True,
        )
    )
    dest = tmp_path / "fix.patch"
    await bugzilla.download_attachment(
        BugzillaContext(client=client), attachment_id=7, dest_path=str(dest)
    )
    assert dest.read_bytes() == b"hello"


async def test_content_type_parameters_and_case_are_ignored(tmp_path):
    client = _attachment_client(
        _att(file_name="steps.txt", content_type="TEXT/PLAIN; charset=UTF-8")
    )
    dest = tmp_path / "steps.txt"
    await bugzilla.download_attachment(
        BugzillaContext(client=client), attachment_id=7, dest_path=str(dest)
    )
    assert dest.read_bytes() == b"hello"


async def test_download_attachment_not_found():
    client = MagicMock()
    client.request.return_value = {"attachments": {}}
    with pytest.raises(ToolError) as ei:
        await bugzilla.download_attachment(
            BugzillaContext(client=client), attachment_id=7, dest_path="/tmp/x"
        )
    assert ei.value.payload["error"] == "attachment_not_found"


async def test_get_bug_attachments_metadata_only_never_asks_for_data():
    client = _attachment_client(_att())
    result = await bugzilla.get_bug_attachments(
        BugzillaContext(client=client), bug_id=1
    )
    assert result["count"] == 1
    assert "data" not in result["attachments"][0]
    assert client.calls == [("bug/1/attachment", {"exclude_fields": "data"})]


def _list_client(atts, *, data="aGVsbG8=", fail_ids=()):
    """A client serving an attachment list, then per-attachment data on demand.

    `fail_ids` makes those attachment IDs raise the way the proxy does for a bug
    the API key cannot reach.
    """
    client = MagicMock()
    calls = []

    def request(path, params=None):
        calls.append((path, params or {}))
        if path == "bug/1/attachment":
            return {"bugs": {"1": [dict(a) for a in atts]}}
        att_id = path.rsplit("/", 1)[1]
        if int(att_id) in fail_ids:
            err = bugsy.BugsyException("nope")
            err.code = 102
            raise err
        source = next(a for a in atts if str(a["id"]) == att_id)
        return {"attachments": {att_id: {**source, "data": data}}}

    client.request.side_effect = request
    client.calls = calls
    return client


async def test_get_bug_attachments_inlines_text_and_refuses_video():
    log = {"id": 7, "file_name": "update.log", "content_type": "text/plain"}
    mp4 = {"id": 8, "file_name": "screencast.mp4", "content_type": "video/mp4"}
    client = _list_client([log, mp4])

    result = await bugzilla.get_bug_attachments(
        BugzillaContext(client=client), bug_id=1, include_data=True
    )

    by_id = {a["id"]: a for a in result["attachments"]}
    assert by_id[7]["data"] == "aGVsbG8="
    assert "data" not in by_id[8]
    assert "video/mp4" in by_id[8]["data_omitted"]
    assert (result["inlined_count"], result["omitted_count"]) == (1, 1)
    # The video is never fetched: one list call plus one fetch for the log.
    assert client.calls == [
        ("bug/1/attachment", {"exclude_fields": "data"}),
        ("bug/attachment/7", {}),
    ]


async def test_get_bug_attachments_does_not_inline_binary_it_would_still_download():
    """A tool result is text, so a base64 png is a long string, not an image block.

    download_attachment plus Read is the only path that reaches the model as an
    image, so inlining one would cost the whole file in tokens for nothing.
    """
    png = {"id": 7, "file_name": "shot.png", "content_type": "image/png"}
    pdf = {"id": 8, "file_name": "report.pdf", "content_type": "application/pdf"}
    svg = {"id": 9, "file_name": "case.svg", "content_type": "image/svg+xml"}
    client = _list_client([png, pdf, svg])

    result = await bugzilla.get_bug_attachments(
        BugzillaContext(client=client), bug_id=1, include_data=True
    )

    by_id = {a["id"]: a for a in result["attachments"]}
    for att_id in (7, 8):
        assert "data" not in by_id[att_id]
        assert "download_attachment" in by_id[att_id]["data_omitted"]
    # SVG is markup, so the text is the content.
    assert by_id[9]["data"] == "aGVsbG8="
    assert (result["inlined_count"], result["omitted_count"]) == (1, 2)
    assert ("bug/attachment/7", {}) not in client.calls
    assert ("bug/attachment/8", {}) not in client.calls

    # Still downloadable to disk, where size does not cost context.
    assert bugzilla.attachment_type_allowed(png) == (True, "image/png")
    assert bugzilla.attachment_type_allowed(pdf) == (True, "application/pdf")


async def test_get_bug_attachments_skips_text_over_the_inline_size_limit():
    big = {
        "id": 7,
        "file_name": "huge.log",
        "content_type": "text/plain",
        "size": bugzilla.MAX_INLINE_BYTES + 1,
    }
    small = {
        "id": 8,
        "file_name": "small.log",
        "content_type": "text/plain",
        "size": bugzilla.MAX_INLINE_BYTES,
    }
    client = _list_client([big, small])

    result = await bugzilla.get_bug_attachments(
        BugzillaContext(client=client), bug_id=1, include_data=True
    )

    by_id = {a["id"]: a for a in result["attachments"]}
    assert "data" not in by_id[7]
    assert "inline limit" in by_id[7]["data_omitted"]
    assert by_id[8]["data"] == "aGVsbG8="
    assert ("bug/attachment/7", {}) not in client.calls


async def test_get_bug_attachments_caps_the_number_it_inlines():
    atts = [
        {"id": i, "file_name": f"{i}.log", "content_type": "text/plain"}
        for i in range(1, bugzilla.MAX_INLINE_ATTACHMENTS + 4)
    ]
    client = _list_client(atts)

    result = await bugzilla.get_bug_attachments(
        BugzillaContext(client=client), bug_id=1, include_data=True
    )

    assert result["inlined_count"] == bugzilla.MAX_INLINE_ATTACHMENTS
    assert result["omitted_count"] == 3
    # Nothing is dropped silently: the ones past the cap say so.
    over = [a for a in result["attachments"] if "data" not in a]
    assert len(over) == 3
    assert all("only the first" in a["data_omitted"] for a in over)


async def test_get_bug_attachments_one_failure_does_not_discard_the_rest():
    """Before the per-attachment loop this was one request that could not fail partway.

    A raise from the loop would throw away everything already fetched, so an
    inaccessible attachment has to degrade to a note on itself.
    """
    a = {"id": 7, "file_name": "a.log", "content_type": "text/plain"}
    b = {"id": 8, "file_name": "b.log", "content_type": "text/plain"}
    c = {"id": 9, "file_name": "c.log", "content_type": "text/plain"}
    client = _list_client([a, b, c], fail_ids=(8,))

    result = await bugzilla.get_bug_attachments(
        BugzillaContext(client=client), bug_id=1, include_data=True
    )

    by_id = {x["id"]: x for x in result["attachments"]}
    assert by_id[7]["data"] == "aGVsbG8="
    assert by_id[9]["data"] == "aGVsbG8="
    assert by_id[8]["data_error"]["error"] == "access_denied"
    assert (result["inlined_count"], result["error_count"]) == (2, 1)


def test_type_resolution_does_not_read_the_system_mime_database(monkeypatch):
    """The verdict must not depend on whether /etc/mime.types knows an extension.

    `.log` resolves to text/plain from /etc/apache2/mime.types on macOS and to
    nothing in the CI and agent containers, so leaning on `mimetypes.guess_type`
    made an update.log readable on a laptop and refused in production. Poison the
    module-level function to prove nothing calls it.
    """

    def boom(*args, **kwargs):
        raise AssertionError("system mime database consulted")

    monkeypatch.setattr(bugzilla.mimetypes, "guess_type", boom)

    for file_name in ("update.log", "patch.diff", "bug.patch"):
        att = {
            "file_name": file_name,
            "content_type": "application/octet-stream",
        }
        assert bugzilla.attachment_type_allowed(att) == (True, "text/plain")

    mp4 = {"file_name": "screencast.mp4", "content_type": "application/octet-stream"}
    assert bugzilla.attachment_type_allowed(mp4) == (False, "video/mp4")
