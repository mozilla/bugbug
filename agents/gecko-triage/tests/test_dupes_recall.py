"""Recall of the concept search on a faked Bugzilla.

The new bug reports that audio fades in after a pause, with Bluetooth headphones. The
earlier report says "headset (... in wireless mode)", "skipping 5/10 seconds" and "fade
back in", and never "Bluetooth". The summary word search finds it only by the word
"fade". The concept search must find it for the real summary and for two rewordings
that do not share that word.
"""

import pytest
from conftest import FakeBugzilla
from hackbot_agents.gecko_triage.dupes import (
    ConceptContext,
    run_concept_search,
    search_scope,
)

NEW_BUG = 9100668
EARLIER_REPORT = 9094151
META = 9080000
HASH_ONLY = 9090002

# The concepts a hunter would choose for each summary: symptom words, the action that
# triggers it, and the device class. No device model and no site name.
CASES = {
    "Bluetooth headphone audio fades in after resuming playback in Firefox": [
        ["fade", "fading", "ramp"],
        ["pause", "unpaus", "resum", "seek", "skip"],
        ["bluetooth", "headset", "headphone", "wireless", "airpod", "earbud"],
    ],
    "Audio is delayed after unpausing a video with wireless headset": [
        ["delay", "lag", "late", "fade", "ramp", "slow"],
        ["unpaus", "resum", "pause", "skip", "seek"],
        ["wireless", "headset", "headphone", "bluetooth", "earbud"],
    ],
    "Sound ramps up slowly after resuming YouTube on AirPods": [
        ["ramp", "slow", "gradual", "fade", "quiet"],
        ["resum", "unpaus", "pause", "seek", "skip"],
        ["airpod", "bluetooth", "headphone", "headset", "earbud", "wireless"],
    ],
}


def _ctx():
    products, components = search_scope(
        {"product": "Core", "component": "Audio/Video: Playback"}
    )
    return ConceptContext(
        url="http://broker/mcp",
        bug_id=NEW_BUG,
        products=products,
        components=components,
    )


@pytest.mark.parametrize("summary", list(CASES))
async def test_the_earlier_report_ranks_in_the_top_three(corpus, summary):
    ctx = _ctx()
    result = await run_concept_search(FakeBugzilla(corpus).search, ctx, CASES[summary])
    ids = [c["id"] for c in result["candidates"]]
    assert EARLIER_REPORT in ids[:3], ids
    assert NEW_BUG not in ids
    found = next(c for c in result["candidates"] if c["id"] == EARLIER_REPORT)
    assert len(found["matched"]) == 3 and "comments" in found["where"]


async def test_the_meta_bug_is_flagged_and_a_hash_is_not_a_word(corpus):
    ctx = _ctx()
    summary = next(iter(CASES))
    result = await run_concept_search(FakeBugzilla(corpus).search, ctx, CASES[summary])
    by_id = {c["id"]: c for c in result["candidates"]}
    assert by_id[META]["meta"] and ctx.meta_ids == {META}
    assert "fade" not in by_id[HASH_ONLY]["matched"]
