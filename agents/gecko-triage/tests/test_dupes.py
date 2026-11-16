"""Tests for the duplicate search: summary words, crash signatures and concepts."""

import asyncio

import pytest
from agent_tools.registry import ToolError
from conftest import FakeBugzilla
from hackbot_agents.gecko_triage import dupes
from hackbot_agents.gecko_triage.dupes import (
    CANDIDATE_FIELDS,
    MAX_CONCEPTS,
    MAX_QUERY_WORDS,
    MAX_SYNONYMS,
    MAX_WORDS,
    MIN_CONCEPTS,
    SEARCH_CONCEPTS_TOOL,
    SEARCH_LIMIT,
    WORD_CAP,
    ConceptContext,
    concept_queries,
    content_words,
    crash_signatures,
    find_candidates,
    gather_within,
    meta_candidates,
    rank_candidates,
    rank_concept_hits,
    rarest_words,
    render_candidates,
    run_concept_search,
    search_concepts_tool,
    signature_query,
    validate_concepts,
    with_signature_matches,
    word_query,
)

PRODUCTS = ["Core"]
COMPONENTS = ["Audio/Video", "Audio/Video: Playback"]
FADE = ["fade", "fading", "ramp"]
PAUSE = ["pause", "unpaus", "resum", "seek", "skip"]
DEVICE = ["bluetooth", "headset", "headphone", "wireless", "airpod", "earbud"]


def _bugs(*ids, **fields):
    return [{"id": n, "summary": str(n), **fields} for n in ids]


def _ctx(**kw):
    return ConceptContext(
        url="http://broker/mcp",
        bug_id=kw.pop("bug_id", 9100668),
        products=PRODUCTS,
        components=COMPONENTS,
        **kw,
    )


def _fake_broker(fake, timeouts=None):
    class Session:
        def __init__(self, url, timeout):
            if timeouts is not None:
                timeouts.append(timeout)

        async def __aenter__(self):
            return fake.search

        async def __aexit__(self, *exc):
            return False

    return Session


def _broken_broker(url, timeout):
    raise RuntimeError("no broker")


def test_content_words_drop_filler_and_stem():
    words = content_words(
        "Bluetooth headphone audio fades in after resuming playback in Firefox"
    )
    assert "fade" in words
    assert "resum" in words
    assert "bluetooth" in words
    for filler in ("in", "after", "firefox"):
        assert filler not in words
    assert len(words) == len(set(words))


def test_content_words_of_an_empty_summary():
    assert content_words("") == []


def test_content_words_are_capped():
    summary = " ".join(f"word{n:02d}x" for n in range(30))
    assert len(content_words(summary)) == MAX_QUERY_WORDS


def test_short_media_terms_are_kept_and_filler_plurals_dropped():
    words = content_words("AV1 MP4 issues: HDR problems in versions")
    assert {"av1", "mp4", "hdr"} <= set(words)
    for filler in ("issue", "problem", "version"):
        assert filler not in words


def test_a_word_query_is_a_whole_word_prefix_search_on_the_summary():
    assert word_query(PRODUCTS, COMPONENTS, "fade") == {
        "product": PRODUCTS,
        "component": COMPONENTS,
        "f1": "short_desc",
        "o1": "regexp",
        "v1": r"\b(fade)",
        "include_fields": CANDIDATE_FIELDS,
        "order": "bug_id DESC",
        "limit": dupes.WORD_QUERY_LIMIT,
    }


def test_a_common_word_is_dropped_and_the_rarest_are_kept():
    hits = {f"w{n}": _bugs(*range(n + 1)) for n in range(MAX_WORDS + 3)}
    hits["audio"] = _bugs(*range(WORD_CAP))
    kept = rarest_words(hits)
    assert "audio" not in kept
    assert kept == [f"w{n}" for n in range(MAX_WORDS)]


def test_a_rare_word_outranks_two_common_ones():
    # "bluetooth" and "headphone" reach the cap, so their count is not known and they
    # are dropped: the bug with the rare symptom word is the better lead.
    common = _bugs(*range(100, 100 + WORD_CAP))
    hits = {
        "bluetooth": common,
        "headphone": common,
        "fade": _bugs(9000002, 5),
    }
    assert rank_candidates(hits, bug_id=0)[0]["id"] == 9000002


def test_two_matched_words_outrank_one_rarer_word():
    hits = {
        "fade": _bugs(9000002),
        "resum": _bugs(9000003, *range(10, 20)),
        "pause": _bugs(9000003, *range(20, 40)),
    }
    ranked = rank_candidates(hits, bug_id=0)
    assert ranked[0]["id"] == 9000003
    assert ranked[0]["words"] == ["resum", "pause"]
    assert ranked[1]["id"] == 9000002


def test_one_rare_symptom_word_outranks_two_common_device_words():
    hits = {
        "bluetooth": _bugs(9000003, *range(100, 171)),
        "headphone": _bugs(9000003, *range(200, 251)),
        "fade": _bugs(9000002, *range(300, 306)),
    }
    assert rank_candidates(hits, bug_id=0)[0]["id"] == 9000002


def test_a_word_with_one_match_does_not_outrank_two_symptom_words():
    hits = {
        "maxwell": _bugs(9000002),
        "fade": _bugs(9000003, *range(10, 19)),
        "unpaus": _bugs(9000003, *range(20, 29)),
    }
    assert rank_candidates(hits, bug_id=0)[0]["id"] == 9000003
    assert dupes.word_weight(1) == dupes.word_weight(dupes.WEIGHT_FLOOR)


def test_candidates_rank_by_rare_words_and_leave_out_the_bug():
    hits = {
        "fade": [
            {"id": 9000002, "summary": "audio fades back in"},
            {"id": 7, "summary": "x"},
        ],
        "resum": [{"id": 9000002, "summary": "audio fades back in"}],
        "audio": [{"id": 9, "summary": "y"}, {"id": 42, "summary": "self"}],
    }
    ranked = rank_candidates(hits, bug_id=42)
    assert ranked[0]["id"] == 9000002 and ranked[0]["words"] == ["fade", "resum"]
    assert 42 not in [c["id"] for c in ranked]


def test_candidates_are_capped():
    hits = {"w": _bugs(*range(30))}
    assert len(rank_candidates(hits, bug_id=0, limit=10)) == 10


def test_candidates_render_for_the_prompt():
    text = render_candidates(
        [
            {
                "id": 9000002,
                "summary": "fades back",
                "status": "UNCONFIRMED",
                "words": ["fade"],
            },
            {
                "id": 9000003,
                "summary": "Crash in [@ Foo]",
                "status": "RESOLVED",
                "resolution": "FIXED",
                "words": [],
                "signatures": ["Foo"],
            },
        ]
    )
    assert "bug 9000002 (UNCONFIRMED)" in text and "words: fade" in text
    assert "bug 9000003 (RESOLVED FIXED)" in text
    assert "crash signature [@ Foo]" in text
    assert "none found" in render_candidates([])


def test_the_search_asks_for_the_fields_the_meta_check_reads():
    assert {"id", "summary", "keywords", "dupe_of"} <= set(CANDIDATE_FIELDS.split(","))


def test_meta_candidates_by_keyword_or_summary():
    candidates = [
        {"id": 1, "summary": "Video stalls", "keywords": ["meta"]},
        {"id": 2, "summary": "[meta] AV1 tracking", "keywords": []},
        {"id": 3, "summary": "Video stalls on resume", "keywords": ["regression"]},
        {"id": 4, "summary": "Audio cuts out"},
    ]
    assert meta_candidates(candidates) == {1, 2}
    assert meta_candidates([]) == frozenset()


def test_crash_signatures_are_read_from_the_field():
    text = "[@ mozilla::AudioSink::PopFrames]\r\n[@ Foo | Bar::Baz ]  [@ ]"
    assert crash_signatures(text) == ["mozilla::AudioSink::PopFrames", "Foo | Bar::Baz"]
    assert crash_signatures("") == crash_signatures(None) == []
    many = "".join(f"[@ f{n}]" for n in range(10))
    assert len(crash_signatures(many)) == dupes.MAX_SIGNATURES


def test_a_signature_query_is_a_substring_search_newest_first():
    assert signature_query(PRODUCTS, COMPONENTS, "Foo | Bar") == {
        "product": PRODUCTS,
        "component": COMPONENTS,
        "f1": "cf_crash_signature",
        "o1": "substring",
        "v1": "Foo | Bar",
        "include_fields": CANDIDATE_FIELDS,
        "order": "bug_id DESC",
        "limit": dupes.SIGNATURE_QUERY_LIMIT,
    }


def _sig(*sigs):
    return {"cf_crash_signature": " ".join(f"[@ {s}]" for s in sigs)}


def test_signature_matches_come_first_and_are_marked():
    ranked = rank_candidates({"fade": _bugs(9000002, 9000003)}, bug_id=1)
    merged = with_signature_matches(
        {
            "Foo": _bugs(9000003, 1, **_sig("Foo"))
            + _bugs(9000001, **_sig("Foo", "Bar")),
            "Bar": _bugs(9000001, **_sig("Foo", "Bar")),
        },
        ranked,
        bug_id=1,
    )
    assert [c["id"] for c in merged] == [9000003, 9000001, 9000002]
    assert merged[0]["signatures"] == ["Foo"] and merged[0]["words"] == ["fade"]
    assert merged[1]["signatures"] == ["Foo", "Bar"]
    assert "signatures" not in merged[2]


def test_a_longer_signature_that_contains_the_bugs_is_not_a_match():
    merged = with_signature_matches(
        {"Foo": _bugs(9000001, **_sig("Foo::Bar")) + _bugs(9000002, **_sig("Foo"))},
        [],
        bug_id=1,
    )
    assert [c["id"] for c in merged] == [9000002]


def test_a_generic_signature_is_not_a_lead():
    common = _bugs(
        *range(9000000, 9000000 + dupes.SIGNATURE_CAP), **_sig("OOM | small")
    )
    ranked = rank_candidates({"fade": _bugs(9100001)}, bug_id=1)
    merged = with_signature_matches({"OOM | small": common}, ranked, bug_id=1)
    assert [c["id"] for c in merged] == [9100001]


def test_a_word_query_asks_for_more_than_the_cap():
    assert dupes.WORD_QUERY_LIMIT > WORD_CAP


async def test_one_failing_query_keeps_the_others():
    async def ok(n):
        return n

    async def bad():
        raise RuntimeError("broker error")

    assert await gather_within([ok(1), bad(), ok(3)], 5) == [1, None, 3]


async def test_a_slow_query_is_dropped_at_the_budget():
    async def slow():
        await asyncio.sleep(60)

    async def ok():
        return "done"

    assert await gather_within([slow(), ok()], 0.05) == [None, "done"]
    assert await gather_within([], 1) == []


async def test_an_outer_timeout_cancels_every_search():
    started = []

    async def slow():
        started.append(asyncio.current_task())
        await asyncio.sleep(10)

    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.05):
            await gather_within([slow(), slow()], 5)
    assert len(started) == 2 and all(t.done() for t in started)


async def test_find_candidates_searches_words_and_signatures(corpus, monkeypatch):
    fake = FakeBugzilla(corpus)
    timeouts = []
    monkeypatch.setattr(dupes, "broker_search", _fake_broker(fake, timeouts))
    bug = {
        "id": 9100668,
        "product": "Core",
        "component": "Audio/Video: Playback",
        "summary": "Audio skips after resuming",
        "cf_crash_signature": "[@ mozilla::AudioSink::PopFrames]",
    }
    found = await find_candidates({"url": "http://broker/mcp"}, bug)
    assert found[0]["id"] == 9088001 and found[0]["signatures"]
    assert {c["id"] for c in found} >= {9091245, 9096008}
    sent = [c.get("v1") for c in fake.calls]
    assert r"\b(skip)" in sent and "mozilla::AudioSink::PopFrames" in sent
    assert timeouts == [dupes.SEARCH_TIMEOUT]


async def test_find_candidates_never_raises(monkeypatch):
    monkeypatch.setattr(dupes, "broker_search", _broken_broker)
    bug = {"id": 1, "summary": "Audio fades", "component": "Audio/Video"}
    assert await find_candidates({"url": "http://broker/mcp"}, bug) == []
    assert await find_candidates({}, bug) == []


def test_good_concepts_are_normalized():
    assert validate_concepts([["Fade", "fading", "fade"], ["PAUSE", "skip"]]) == [
        ["fade", "fading"],
        ["pause", "skip"],
    ]


@pytest.mark.parametrize(
    "concepts",
    [
        [["fade"]],
        [["fade"]] * (MAX_CONCEPTS + 1),
        [["fade"], [f"s{n}xx" for n in range(MAX_SYNONYMS + 1)]],
        [["fade"], []],
        [["fa"], ["pause"]],
        [["fade|x"], ["pause"]],
        [["fade)"], ["pause"]],
        [["fa de"], ["pause"]],
        [["fade"], ["pause.*"]],
        [["f" * 40], ["pause"]],
        [["fade"], "pause"],
        "fade",
        [["fade"], [3]],
    ],
)
def test_bad_concepts_are_refused(concepts):
    with pytest.raises(ValueError):
        validate_concepts(concepts)


def test_concept_limits_are_named():
    assert (MIN_CONCEPTS, MAX_CONCEPTS, MAX_SYNONYMS) == (2, 4, 8)
    assert dupes.MIN_CONCEPT_STEM == 3


def test_two_concepts_make_one_query_on_summaries_and_one_on_comments():
    queries = concept_queries(PRODUCTS, COMPONENTS, 9100668, [FADE, PAUSE[:2]])
    assert [(groups, field) for groups, field, _ in queries] == [
        ((0, 1), "short_desc"),
        ((0, 1), "longdesc"),
    ]
    assert queries[1][2] == {
        "product": PRODUCTS,
        "component": COMPONENTS,
        "f1": "longdesc",
        "o1": "regexp",
        "v1": r"\b(fade|fading|ramp)",
        "f2": "longdesc",
        "o2": "regexp",
        "v2": r"\b(pause|unpaus)",
        "f3": "bug_id",
        "o3": "notequals",
        "v3": 9100668,
        "include_fields": CANDIDATE_FIELDS,
        "order": "bug_id DESC",
        "limit": SEARCH_LIMIT,
    }


def test_more_concepts_query_every_pair_and_all_of_them():
    three = concept_queries(PRODUCTS, COMPONENTS, 1, [FADE, PAUSE, DEVICE])
    groups = [g for g, field, _ in three if field == "short_desc"]
    assert groups == [(0, 1), (0, 2), (1, 2), (0, 1, 2)]
    assert len(three) == 8
    four = concept_queries(PRODUCTS, COMPONENTS, 1, [FADE, PAUSE, DEVICE, ["lag"]])
    assert len(four) == 14


def test_concept_hits_rank_by_groups_then_summary_then_newest():
    concepts = [FADE, PAUSE, DEVICE]
    answers = [
        ((0, 1), "short_desc", _bugs(5, 6)),
        ((0, 1), "longdesc", _bugs(6, 7, 9)),
        ((1, 2), "longdesc", _bugs(7, 8, 9100668)),
        ((0, 2), "longdesc", None),
    ]
    ranked = rank_concept_hits(concepts, answers, bug_id=9100668)
    assert [c["id"] for c in ranked] == [7, 6, 5, 9, 8]
    top = ranked[0]
    assert top["matched"] == ["fade", "pause", "bluetooth"]
    assert top["where"] == ["comments"]
    assert ranked[1]["where"] == ["summary", "comments"]
    assert 9100668 not in [c["id"] for c in ranked]


def test_concept_hits_flag_meta_bugs_and_list_them_last():
    answers = [
        ((0, 1), "short_desc", [{"id": 9, "summary": "[meta] Audio", "keywords": []}]),
        ((0, 1), "longdesc", [{"id": 3, "summary": "Fades", "keywords": []}]),
    ]
    ranked = rank_concept_hits([FADE, PAUSE], answers, bug_id=1)
    assert [(c["id"], c["meta"]) for c in ranked] == [(3, False), (9, True)]


def test_concept_hits_are_capped_and_keep_the_status_fields():
    bugs = _bugs(*range(1, 30), status="RESOLVED", resolution="DUPLICATE", dupe_of=99)
    ranked = rank_concept_hits([FADE, PAUSE], [((0, 1), "short_desc", bugs)], 0)
    assert len(ranked) == dupes.MAX_CONCEPT_RESULTS
    assert {"status", "resolution", "dupe_of"} <= set(ranked[0])
    assert "keywords" not in ranked[0]


async def test_a_concept_search_feeds_the_meta_ids_to_the_record_hooks(corpus):
    ctx = _ctx()
    result = await run_concept_search(
        FakeBugzilla(corpus).search, ctx, [FADE, PAUSE, DEVICE]
    )
    assert 9080000 in ctx.meta_ids
    assert result["searched"] == result["answered"] == 8
    assert "note" not in result


async def test_a_slow_or_failing_concept_query_does_not_fail_the_search(monkeypatch):
    monkeypatch.setattr(dupes, "CONCEPT_BUDGET", 0.05)
    calls = []

    async def search(params):
        calls.append(params)
        if len(calls) == 1:
            return [{"id": 5, "summary": "fades after pause", "keywords": []}]
        if params["f1"] == "longdesc":
            await asyncio.sleep(60)
        raise RuntimeError("broker error")

    result = await run_concept_search(search, _ctx(), [FADE, PAUSE, DEVICE])
    assert result["searched"] == 8 and result["answered"] == 1
    assert "7 of 8" in result["note"]
    assert [c["id"] for c in result["candidates"]] == [5]


async def test_the_concept_tool_refuses_bad_concepts():
    with pytest.raises(ToolError) as e:
        await search_concepts_tool(_ctx(), [["fade|.*"], ["pause"]])
    assert e.value.payload["error"] == "invalid_concepts"


async def test_the_concept_tool_never_raises_on_a_broker_failure(monkeypatch):
    monkeypatch.setattr(dupes, "broker_search", _broken_broker)
    result = await search_concepts_tool(_ctx(), [FADE, PAUSE])
    assert result["candidates"] == [] and "no broker" in result["note"]


async def test_the_concept_tool_searches_the_broker(corpus, monkeypatch):
    fake = FakeBugzilla(corpus)
    timeouts = []
    monkeypatch.setattr(dupes, "broker_search", _fake_broker(fake, timeouts))
    result = await search_concepts_tool(_ctx(), [FADE, PAUSE, DEVICE])
    assert result["candidates"][0]["id"] == 9094151
    assert len(fake.calls) == 8
    assert timeouts == [dupes.CONCEPT_BUDGET + dupes.CONNECT_GRACE]


def test_the_concept_tool_name_and_server():
    assert SEARCH_CONCEPTS_TOOL == "mcp__dupes__search_concepts"
    server = dupes.concepts_server(_ctx())
    assert server["type"] == "sdk" and server["name"] == "dupes"
