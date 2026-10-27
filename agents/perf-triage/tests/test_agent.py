from __future__ import annotations

import tempfile
from pathlib import Path

from hackbot_agents.perf_triage.agent import run_perf_triage
from hackbot_runtime.actions import ActionsRecorder
from performance_tools.triage import TriageResult

BUG = 1839859
SEL = {
    "platforms": [
        "platform-windows",
        "platform-macos",
        "platform-linux",
        "platform-android",
    ],
    "browser": ["browser-minor"],
    "site": ["site-major"],
    "configuration": ["scenario-general"],
    "pageload": ["pageload-none"],
    "pages": ["pages-rare"],
    "resource": ["resource-none"],
    "reproducible": ["reproducible-reproduced"],
    "additional": [],
}


def triage(**kw) -> TriageResult:
    base = dict(
        impact="low",
        score=7.2,
        keywords=["perf:responsiveness", "reproducible"],
        severity="S3",
        comment="rated **low**",
        selections=SEL,
        unestablished=[],
    )
    base.update(kw)
    return TriageResult(**base)


async def run(result: TriageResult, *, impact="?", comments=(), url="", attachments=()):
    recorder = ActionsRecorder(artifacts_dir=Path(tempfile.mkdtemp()))
    seen: dict = {}

    async def fake_fetch_bug(_server, _bug):
        return {
            "id": BUG,
            "product": "Core",
            "component": "DOM: Core & HTML",
            "cf_performance_impact": impact,
            "keywords": [],
            "url": url,
        }

    async def fake_fetch_comments(_server, _bug):
        return list(comments)

    async def fake_fetch_attachments(_server, _bug):
        return list(attachments)

    async def fake_triage(profile_urls):
        seen["profile_urls"] = profile_urls
        return result

    findings = await run_perf_triage(
        bug_id=BUG,
        bugzilla_mcp_server={"type": "http", "url": "http://broker.invalid/mcp"},
        recorder=recorder,
        triage=fake_triage,
        fetch_bug=fake_fetch_bug,
        fetch_comments=fake_fetch_comments,
        fetch_attachments=fake_fetch_attachments,
    )
    return findings, recorder, seen


def types(recorder):
    return [a["type"] for a in recorder.actions]


async def test_failed_triage_records_nothing():
    f, r, _ = await run(
        triage(impact="none", score=0, comment="", failure_reason="no bug")
    )
    assert r.actions == []
    assert not f.auto_apply


async def test_full_mode_confident_records_both_and_auto_applies():
    f, r, _ = await run(triage())
    assert types(r) == ["bugzilla.add_comment", "bugzilla.update_bug"]
    assert r.actions[1]["params"]["changes"] == {
        "cf_performance_impact": "low",
        "keywords": {"add": ["perf:responsiveness", "reproducible"]},
    }
    assert f.auto_apply
    assert f.write_mode == "full"


async def test_no_keywords_omits_the_keywords_change():
    _, r, _ = await run(triage(keywords=[]))
    assert r.actions[1]["params"]["changes"] == {"cf_performance_impact": "low"}


async def test_unstable_records_but_holds():
    f, r, _ = await run(triage(unestablished=["pages-affected"]))
    assert len(r.actions) == 2
    assert not f.auto_apply
    assert f.stability["flipping"] == ["pages-affected"]


async def test_comment_only_agreement_records_nothing():
    f, r, _ = await run(triage(), impact="low")
    assert r.actions == []
    assert f.skipped_reason == "agrees with existing rating"
    assert not f.auto_apply


async def test_comment_only_disagreement_records_comment_only():
    f, r, _ = await run(triage(), impact="medium")
    assert types(r) == ["bugzilla.add_comment"]
    assert "Existing rating: **medium**" in r.actions[0]["params"]["text"]
    assert f.auto_apply


async def test_comment_only_unstable_disagreement_holds():
    f, r, _ = await run(triage(unestablished=["pages-affected"]), impact="medium")
    assert types(r) == ["bugzilla.add_comment"]
    assert not f.auto_apply


async def test_profile_urls_reach_triage():
    _, _, seen = await run(triage(), comments=["see https://share.firefox.dev/44agmdV"])
    assert seen["profile_urls"] == ["https://share.firefox.dev/44agmdV"]


async def test_no_profile_urls_when_none_linked():
    _, _, seen = await run(triage())
    assert seen["profile_urls"] == []


async def test_failed_bug_lookup_records_nothing_and_skips_triage():
    # A broker failure returns {}; that must not read as "unrated" and let a
    # confident run overwrite a rating a person already set.
    recorder = ActionsRecorder(artifacts_dir=Path(tempfile.mkdtemp()))
    called = []

    async def empty(_s, _b):
        return {}

    async def no_comments(_s, _b):
        return []

    async def fake_triage(urls):
        called.append(urls)
        return triage()

    f = await run_perf_triage(
        bug_id=BUG,
        bugzilla_mcp_server={},
        recorder=recorder,
        triage=fake_triage,
        fetch_bug=empty,
        fetch_comments=no_comments,
    )
    assert recorder.actions == []
    assert not f.auto_apply
    assert f.skipped_reason == "bug lookup failed"
    assert called == []


async def test_high_needinfos_the_reviewer():
    _, r, _ = await run(triage(impact="high", score=60, keywords=[]))
    assert r.actions[1]["params"]["changes"]["flags"] == [
        {"name": "needinfo", "status": "?", "requestee": "bas@basschouten.com"}
    ]


async def test_below_high_sets_no_flag():
    _, r, _ = await run(triage(impact="medium", score=30))
    assert "flags" not in r.actions[1]["params"]["changes"]
