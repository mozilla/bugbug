from datetime import UTC, datetime

import pytest

from hackbot_agents.crash_info.sources import LeakageBlocked
from hackbot_agents.crash_info.sources.bugzilla import (
    BugView,
    Comment,
    cutoff_before_first_patch,
    parse_signatures,
    rollback_fields,
)
from hackbot_agents.crash_info.sources.code import focus_line
from hackbot_agents.crash_info.sources.crash_stats import crash_date

from .conftest import BUG, CUTOFF, HISTORY, NEW_REV, OLD_REV


def test_rollback_undoes_later_changes_only():
    fields = {
        "status": "RESOLVED",
        "resolution": "FIXED",
        "severity": "S2",
        "keywords": ["crash", "regression"],
        "dupe_of": None,
    }
    rolled, names = rollback_fields(fields, HISTORY, CUTOFF)
    assert rolled["status"] == "NEW" and rolled["resolution"] == ""
    assert rolled["severity"] == "S2"  # changed before the cutoff
    assert rolled["keywords"] == ["crash"]
    assert "dupe_of" not in rolled
    assert names == ["keywords", "resolution", "status"]


def test_signatures_and_first_patch():
    assert parse_signatures("[@ a::b]\r\n[@ c | d ]") == ["a::b", "c | d"]
    landing = Comment(
        1,
        3,
        "pulsebot",
        datetime(2026, 5, 9, tzinfo=UTC),
        "Pushed by x: https://hg.mozilla.org/integration/autoland/rev/abc",
    )
    view = BugView(fields={}, comments=[landing], attachments=[])
    assert cutoff_before_first_patch(view) == datetime(
        2026, 5, 8, 23, 59, 59, tzinfo=UTC
    )


async def test_bug_view_as_of_and_search_filters(sources):
    view = await sources.bugzilla.view(BUG, CUTOFF)
    assert [c.count for c in view.comments] == [0, 1]
    assert view.attachments == []
    assert view.fields["status"] == "NEW"
    related = await sources.bugzilla.search_signature(
        "Foo::Bar", exclude=BUG, as_of=CUTOFF
    )
    assert [b["id"] for b in related] == [999999]
    assert "status" not in related[0]  # later status could leak the outcome
    live = await sources.bugzilla.view(BUG)
    assert len(live.comments) == 3


async def test_quicksearch_quotes_field_like_terms(sources, transport):
    seen = []
    original = transport.handler

    def spy(request):
        seen.append(request.url.params.get("quicksearch"))
        return original(request)

    transport.handler = spy
    await sources.bugzilla.quicksearch("nsINode::OwnerDoc crash")
    assert seen == ['"nsINode::OwnerDoc" crash']


async def test_revision_at_picks_last_push_before_cutoff(sources):
    assert await sources.hgmo.revision_at(CUTOFF) == OLD_REV


async def test_pinned_search_never_shows_post_cutoff_lines(sources):
    await sources.pin_code(CUTOFF)
    hits = await sources.code.search("mWidget")
    assert [(h.path, h.line) for h in hits] == [("dom/foo/Foo.cpp", 42)]
    assert all("FIXED_LINE" not in h.text for h in hits)
    with pytest.raises(LeakageBlocked):
        await sources.code.commit(NEW_REV)
    assert (await sources.code.commit("c" * 40))["diff"].startswith("diff --git")


async def test_read_recentres_on_the_function(sources):
    await sources.pin_code(CUTOFF)
    excerpt = await sources.code.read(
        "dom/foo/Foo.cpp", 5, context=3, focus="mozilla::Foo::Bar"
    )
    assert "void Foo::Bar()" in excerpt.text


def test_focus_line_keeps_a_line_already_near_the_function():
    text = "\n".join(["x"] * 10 + ["void Foo::Bar() {", "  y;", "}"])
    assert focus_line(text, 12, "Foo::Bar", 5) == 12
    assert focus_line(text, 1, "Foo::Bar", 2) == 11


async def test_crash_stats_respects_cutoff(sources):
    facets = await sources.crash_stats.signature_facets(
        "mozilla::Foo::Bar", as_of=CUTOFF
    )
    assert facets["total"] == 100 and "address" in facets["facets"]
    assert (
        crash_date("aaaaaaaa-bbbb-cccc-dddd-eeeeee260501").isoformat() == "2026-05-01"
    )
    with pytest.raises(LeakageBlocked):
        await sources.crash_stats.crash_report(
            "aaaaaaaa-bbbb-cccc-dddd-eeeeee260601", as_of=CUTOFF
        )
    report = await sources.crash_stats.crash_report(
        "aaaaaaaa-bbbb-cccc-dddd-eeeeee260501", as_of=CUTOFF
    )
    assert report["frames"][0].startswith("0 xul.dll mozilla::Foo::Bar()")


async def test_crash_stats_lists_a_signature_s_latest_crashes(sources):
    crashes = await sources.crash_stats.signature_crashes(
        "mozilla::Foo::Bar", as_of=CUTOFF
    )
    assert crashes == [
        {"uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeee260501", "date": "2026-05-01"}
    ]


async def test_a_signature_brings_the_stack_of_its_latest_crash(sources):
    from hackbot_agents.crash_info.evidence import EvidenceStore
    from hackbot_agents.crash_info.models import ActionType, CrashInformationState
    from hackbot_agents.crash_info.recovery import Lookup, Recovery

    state = CrashInformationState(bug_id=BUG, taxonomy_version="5", as_of=CUTOFF)
    store = EvidenceStore(state)
    # No crash has this signature exactly: its specific frame finds them.
    signature = "mozilla::detail::InvalidArrayIndex_CRASH | mozilla::Foo::Bar"
    lookup = Lookup(ActionType.INSPECT_CRASH_EVIDENCE, {"signature": signature})
    result = await Recovery(state, store, sources).run(lookup, "act-001")
    assert result.outcome == "new_evidence", result
    stats, crash = (store.get(i) for i in result.evidence_ids)
    assert "contains 'mozilla::Foo::Bar'" in stats.summary
    assert crash.kind == "crash_report" and "latest crash" in crash.summary
    assert "Crashing thread frames:" in crash.content
    assert "mozilla::Foo::Bar() (hg:" in crash.content


async def test_a_crash_report_that_is_gone_falls_back_on_its_signature(sources):
    from hackbot_agents.crash_info.evidence import EvidenceStore
    from hackbot_agents.crash_info.models import (
        ActionType,
        CrashInformationState,
        ReportSnapshot,
    )
    from hackbot_agents.crash_info.recovery import Lookup, Recovery

    state = CrashInformationState(
        bug_id=BUG,
        taxonomy_version="5",
        as_of=CUTOFF,
        report_snapshot=ReportSnapshot(
            bug_id=BUG, url="u", summary="s", crash_signatures=["mozilla::Foo::Bar"]
        ),
    )
    gone = {"crash_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeee260401"}
    lookup = Lookup(ActionType.INSPECT_CRASH_EVIDENCE, gone)
    store = EvidenceStore(state)
    result = await Recovery(state, store, sources).run(lookup, "act-001")
    (other,) = (store.get(i) for i in result.evidence_ids)
    assert other.summary.startswith(
        "crash report aaaaaaaa-bbbb-cccc-dddd-eeeeee260501 (2026-05-01), instead of "
        "crash aaaaaaaa-bbbb-cccc-dddd-eeeeee260401, which is gone: the latest crash"
    )
    # Without crash data completion: not found, as before.
    off = Recovery(state, store, sources, crash_data=False)
    assert (await off.run(lookup, "act-002")).outcome == "no_new_evidence"
    facets_only = Lookup(
        ActionType.INSPECT_CRASH_EVIDENCE, {"signature": "mozilla::Foo::Bar"}
    )
    assert len((await off.run(facets_only, "act-003")).evidence_ids) == 1


def test_function_span_ignores_braces_in_strings_and_comments():
    from hackbot_agents.crash_info.sources.code import definition_line, function_span

    lines = [
        "// helper",
        "void Foo::Bar(int a) {",
        '  printf("{");  // }',
        "  if (a) {",
        "    Baz();",
        "  }",
        "}",
        "void Foo::Other();",
    ]
    start = definition_line(lines, "mozilla::Foo::Bar", near=5)
    assert start == 2
    assert function_span(lines, start) == (1, 7)
    assert function_span(lines, None) is None


async def test_a_passing_406_is_retried(monkeypatch):
    """hg.mozilla.org's edge refuses with 406 when several runs query it at once."""
    import asyncio

    import httpx

    from hackbot_agents.crash_info.sources.http import HttpClient

    answers = iter([406, 406, 200])

    def handler(request):
        return httpx.Response(next(answers), text="ok")

    waits = []

    async def no_wait(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    client = HttpClient(
        user_agent="test",
        timeout_s=5,
        cache_dir=None,
        cache_ttl_s=0,
        transport=httpx.MockTransport(handler),
    )
    try:
        assert await client.get_text("https://hg-edge.mozilla.org/x") == "ok"
        assert client.requests_made == 3
        # A rate limit is waited out longer than a server error would be.
        assert waits == [5.0, 20.0]
    finally:
        await client.aclose()
