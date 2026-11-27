"""Offline fixtures: a fake Mozilla (Bugzilla, crash-stats, Searchfox, hg) behind
httpx.MockTransport; tests/test_react.py scripts the model.

The fake bug 1000001 has a fix that lands after the cutoff: a comment
explaining it (SECRET_FIX_DETAIL), a patch attachment, a status change, and a
code line (FIXED_LINE) that only exists in the current tree. None of them may
ever reach a prompt in as-of mode.
"""

import re
from datetime import UTC, datetime

import httpx
import pytest

from hackbot_agents.crash_info.settings import load_settings
from hackbot_agents.crash_info.sources import build_sources

BUG = 1000001
CUTOFF = datetime(2026, 5, 8, 11, 59, 59, tzinfo=UTC)
OLD_REV = "a" * 40
NEW_REV = "b" * 40


def ts(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


BUG_FIELDS = {
    "id": BUG,
    "summary": "Crash in [@ mozilla::Foo::Bar]",
    "status": "RESOLVED",
    "resolution": "FIXED",
    "product": "Core",
    "component": "DOM: Core",
    "creation_time": "2026-05-01T10:00:00Z",
    "cf_crash_signature": "[@ mozilla::Foo::Bar]",
    "keywords": ["crash", "regression"],
    "severity": "S2",
    "priority": "P2",
    "platform": "x86_64",
    "op_sys": "Windows 11",
    "version": "unspecified",
    "type": "defect",
    "regressed_by": [],
    "depends_on": [],
    "blocks": [],
    "see_also": [],
    "whiteboard": "",
}
HISTORY = [
    {
        "when": "2026-05-02T09:00:00Z",
        "who": "triager",
        "changes": [{"field_name": "severity", "removed": "--", "added": "S2"}],
    },
    {
        "when": "2026-05-10T09:00:00Z",
        "who": "dev",
        "changes": [
            {"field_name": "status", "removed": "NEW", "added": "RESOLVED"},
            {"field_name": "resolution", "removed": "", "added": "FIXED"},
            {"field_name": "keywords", "removed": "", "added": "regression"},
        ],
    },
]
COMMENTS = [
    {
        "id": 1,
        "count": 0,
        "creator": "reporter@example.com",
        "creation_time": "2026-05-01T10:00:00Z",
        "is_private": False,
        "text": (
            "Crash report: https://crash-stats.mozilla.org/report/index/"
            "aaaaaaaa-bbbb-cccc-dddd-eeeeee260501\n\nReason: EXCEPTION_ACCESS_VIOLATION_READ\n\n"
            "Top frames:\n0  xul.dll  mozilla::Foo::Bar()  dom/foo/Foo.cpp:42\n"
            "1  xul.dll  mozilla::Baz::Run()  dom/foo/Baz.cpp:10\n\n`mWidget` seems to be null?"
        ),
    },
    {
        "id": 2,
        "count": 1,
        "creator": "triager@example.com",
        "creation_time": "2026-05-02T09:00:00Z",
        "is_private": False,
        "text": "Also seen in bug 999999.",
    },
    {
        "id": 3,
        "count": 2,
        "creator": "dev@example.com",
        "creation_time": "2026-05-09T09:00:00Z",
        "is_private": False,
        "text": "The fix null-checks mWidget: it is destroyed at shutdown. SECRET_FIX_DETAIL",
    },
]
ATTACHMENTS = [
    {
        "id": 77,
        "creation_time": "2026-05-08T12:00:00Z",
        "description": "Bug 1000001 - Null-check mWidget in Foo::Bar",
        "content_type": "text/x-phabricator-request",
        "file_name": "phabricator-D1-url.txt",
        "is_patch": 0,
        "is_obsolete": 0,
    }
]
# An earlier bug fixed before the cutoff (with a comment made after it, which must
# never reach a prompt), and search results the cutoff must exclude.
PRIOR_COMMENTS = [
    {
        **COMMENTS[0],
        "count": 0,
        "creation_time": "2026-04-01T00:00:00Z",
        "text": "Crash at shutdown in Foo::Bar.",
    },
    {
        **COMMENTS[0],
        "id": 11,
        "count": 1,
        "creation_time": "2026-04-10T00:00:00Z",
        "text": "mWidget is destroyed in Shutdown() before Bar runs.",
    },
    {
        **COMMENTS[0],
        "id": 12,
        "count": 2,
        "creation_time": "2026-04-20T00:00:00Z",
        "text": (
            "Pushed by dev@example.com:\n"
            "https://hg.mozilla.org/integration/autoland/rev/cccccccccccc\n"
            "Bug 999999 - Clear mWidget before shutdown in Foo::Bar. r=reviewer"
        ),
    },
    {
        **COMMENTS[0],
        "id": 13,
        "count": 3,
        "creation_time": "2026-05-20T00:00:00Z",
        "text": "Regressed again. PRIOR_LATE_DETAIL",
    },
]
_PRIOR = {
    "product": "Core",
    "component": "DOM: Core",
    "cf_crash_signature": "[@ mozilla::Foo::Bar]",
}
PRIOR_BUGS = [
    {
        **_PRIOR,
        "id": 999999,
        "summary": "Older crash in Foo::Bar",
        "creation_time": "2026-04-01T00:00:00Z",
        "cf_last_resolved": "2026-04-21T00:00:00Z",
    },
    {
        **_PRIOR,
        "id": 999998,
        "summary": "Crash in Foo::Bar resolved after the cutoff",
        "creation_time": "2026-04-02T00:00:00Z",
        "cf_last_resolved": "2026-05-20T00:00:00Z",
    },
    {
        **_PRIOR,
        "id": 1000002,
        "summary": "Later crash",
        "creation_time": "2026-06-01T00:00:00Z",
        "cf_last_resolved": "2026-06-02T00:00:00Z",
    },
    {
        **_PRIOR,
        "id": BUG,
        "summary": BUG_FIELDS["summary"],
        "creation_time": "2026-05-01T10:00:00Z",
        "cf_last_resolved": "2026-05-10T09:00:00Z",
    },
]
FOO_AT_OLD_REV = "\n".join(
    [f"// line {n}" for n in range(1, 40)]
    + ["void Foo::Bar() {", "  // paint the widget", "  mWidget->Paint();", "}"]
)


def _json(data: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data)


def fake_mozilla(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "bugzilla.mozilla.org":
        if path == f"/rest/bug/{BUG}":
            return _json({"bugs": [BUG_FIELDS]})
        if path == f"/rest/bug/{BUG}/comment":
            return _json({"bugs": {str(BUG): {"comments": COMMENTS}}})
        if path == f"/rest/bug/{BUG}/attachment":
            return _json({"bugs": {str(BUG): ATTACHMENTS}})
        if path == f"/rest/bug/{BUG}/history":
            return _json({"bugs": [{"id": BUG, "history": HISTORY}]})
        if path == "/rest/bug/999999":
            return _json(
                {
                    "bugs": [
                        {
                            **BUG_FIELDS,
                            "id": 999999,
                            "summary": "Older crash in Foo::Bar",
                            "creation_time": "2026-04-01T00:00:00Z",
                        }
                    ]
                }
            )
        if path == "/rest/bug/999999/comment":
            return _json({"bugs": {"999999": {"comments": PRIOR_COMMENTS}}})
        if path == "/rest/bug/999999/attachment":
            return _json({"bugs": {"999999": []}})
        if path == "/rest/bug/999999/history":
            return _json({"bugs": [{"id": 999999, "history": []}]})
        if path == "/rest/bug" and request.url.params.get("chfield") == "resolution":
            # Earlier bugs whose resolution changed to FIXED before a date.
            return _json({"bugs": PRIOR_BUGS})
        if path == "/rest/bug":
            return _json(
                {
                    "bugs": [
                        {
                            "id": 999999,
                            "summary": "Older crash in Foo::Bar",
                            "creation_time": "2026-04-01T00:00:00Z",
                            "status": "NEW",
                            "resolution": "",
                            "product": "Core",
                            "component": "DOM: Core",
                        },
                        {
                            "id": 1000002,
                            "summary": "Later crash",
                            "creation_time": "2026-06-01T00:00:00Z",
                            "status": "NEW",
                            "resolution": "",
                            "product": "Core",
                            "component": "DOM: Core",
                        },
                        {
                            "id": BUG,
                            "summary": BUG_FIELDS["summary"],
                            "creation_time": "2026-05-01T10:00:00Z",
                            "status": "NEW",
                            "resolution": "",
                            "product": "Core",
                            "component": "DOM: Core",
                        },
                    ]
                }
            )
    if host == "crash-stats.mozilla.org":
        if path == "/api/SuperSearch/":
            # A signature no crash has exactly: found by its specific frames only.
            if request.url.params.get("signature", "").startswith("=mozilla::detail"):
                return _json({"total": 0, "facets": {}, "hits": []})
            hits = (
                [{"uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeee260501", "date": "2026-05-01"}]
                if "_columns" in request.url.params
                else []
            )
            return _json(
                {
                    "hits": hits,
                    "total": 100,
                    "facets": {
                        "product": [
                            {"term": "Firefox", "count": 95},
                            {"term": "Thunderbird", "count": 5},
                        ],
                        "address": [{"term": "0x0000000000000000", "count": 90}],
                        "startup_crash": [{"term": "F", "count": 100}],
                        "process_type": [{"term": "parent", "count": 100}],
                        "shutdown_progress": [
                            {"term": "profile-before-change", "count": 80}
                        ],
                    },
                }
            )
        if path == "/api/ProcessedCrash/":
            if request.url.params.get("crash_id", "").endswith("260401"):
                return _json({"error": "not found"}, status=404)  # expired
            return _json(
                {
                    "signature": "mozilla::Foo::Bar",
                    "product": "Firefox",
                    "reason": "EXCEPTION_ACCESS_VIOLATION_READ",
                    "crashing_thread": 0,
                    "json_dump": {
                        "threads": [
                            {
                                "frames": [
                                    {
                                        "frame": 0,
                                        "module": "xul.dll",
                                        "function": "mozilla::Foo::Bar()",
                                        "file": "hg:hg.mozilla.org/mozilla-central:dom/foo/Foo.cpp:abc",
                                        "line": 42,
                                    },
                                    {
                                        "frame": 1,
                                        "module": "xul.dll",
                                        "function": "mozilla::Foo::Run()",
                                        "file": "hg:hg.mozilla.org/mozilla-central:dom/foo/Foo.cpp:abc",
                                        "line": 80,
                                    },
                                ]
                            }
                        ]
                    },
                }
            )
    if host == "searchfox.org" and path == "/firefox-main/search":
        return _json(
            {
                "*title*": "search",
                "normal": {
                    "Definitions (mozilla::Foo::Bar)": [
                        {
                            "path": "dom/foo/Foo.cpp",
                            "lines": [{"lno": 40, "line": "void Foo::Bar() {"}],
                        }
                    ],
                    "Textual Occurrences": [
                        {
                            "path": "dom/foo/Foo.cpp",
                            "lines": [
                                {
                                    "lno": 42,
                                    "line": "  if (!mWidget) return;  // FIXED_LINE",
                                },
                                {"lno": 43, "line": "  mWidget->Paint();"},
                            ],
                        },
                        {
                            "path": "dom/foo/New.cpp",
                            "lines": [{"lno": 1, "line": "mWidget only exists later"}],
                        },
                    ],
                },
            }
        )
    if host in ("hg.mozilla.org", "hg-edge.mozilla.org"):
        if path.endswith("/json-pushes"):
            return _json(
                {
                    "lastpushid": 6,
                    "pushes": {
                        "4": {
                            "date": ts("2026-05-07T00:00:00Z"),
                            "changesets": [OLD_REV],
                        },
                        # A release tag: no source tree behind it.
                        "5": {
                            "date": ts("2026-05-08T06:00:00Z"),
                            "changesets": [
                                {"node": "c" * 40, "branch": "tags-unified"}
                            ],
                        },
                        "6": {
                            "date": ts("2026-05-09T00:00:00Z"),
                            "changesets": [{"node": NEW_REV, "branch": "default"}],
                        },
                    },
                }
            )
        match = re.match(r"/mozilla-central/raw-file/(\w+)/(.+)", path)
        if match:
            rev, file = match.groups()
            if rev == OLD_REV and file == "dom/foo/Foo.cpp":
                return httpx.Response(200, text=FOO_AT_OLD_REV)
            return httpx.Response(404, text="not found")
        if "/json-log/" in path:
            return _json(
                {
                    "entries": [
                        {
                            "node": "c" * 40,
                            "date": [ts("2026-04-20T00:00:00Z"), 0],
                            "user": "dev",
                            "desc": "Bug 990000 - Refactor Foo",
                        }
                    ]
                }
            )
        match = re.match(
            r"/(?:mozilla-central|integration/autoland|comm-central)/json-rev/(\w+)",
            path,
        )
        if match:
            node = match.group(1)
            pushed = (
                ts("2026-05-09T00:00:00Z")
                if node == NEW_REV
                else ts("2026-04-20T00:00:00Z")
            )
            return _json(
                {
                    "node": node,
                    "user": "dev",
                    "desc": "Bug 990000 - Refactor Foo",
                    "date": [pushed, 0],
                    "pushdate": [pushed, 0],
                    "files": [{"file": "dom/foo/Foo.cpp"}],
                }
            )
        if "/raw-rev/" in path:
            return httpx.Response(
                200, text="diff --git a/dom/foo/Foo.cpp b/dom/foo/Foo.cpp\n-old\n+new\n"
            )
    return httpx.Response(404, text=f"unexpected request {request.url}")


@pytest.fixture
def transport() -> httpx.MockTransport:
    return httpx.MockTransport(fake_mozilla)


@pytest.fixture
def settings():
    s = load_settings(
        overrides={
            "llm": {"provider": "ollama"},
            # The scripted runs finish once: the review before finishing has tests
            # of its own (test_react).
            "investigation": {"max_tool_calls": 6, "answer_review": False},
        }
    )
    s.sources.cache_dir = None
    return s


@pytest.fixture
async def sources(settings, transport):
    built = build_sources(settings.sources, transport=transport)
    yield built
    await built.aclose()
