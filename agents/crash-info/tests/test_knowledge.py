"""Earlier bugs as knowledge: what counts as a landing, a signature, a distinctive word."""

from datetime import UTC, datetime

from hackbot_agents.crash_info.knowledge import (
    PriorBugs,
    PriorCase,
    _informative,
    _is_landing,
)
from hackbot_agents.crash_info.models import CrashInformationState, ReportSnapshot


def test_only_landings_count_as_fixes():
    assert _is_landing(
        "Pushed by dev@example.com:\n"
        "https://github.com/mozilla-firefox/firefox/commit/d105d075c6ca\n"
        "https://hg.mozilla.org/integration/autoland/rev/8c3959e65c1c\n"
        "Check editing host on Thunderbird' mail composer. r=masayuki"
    )
    assert _is_landing("https://hg.mozilla.org/mozilla-central/rev/8c3959e65c1c")
    # A bisection names the changeset that caused the bug, not the fix.
    assert not _is_landing(
        "JSBugMon: Bisection requested, result:\nThe first bad revision is:\n"
        "changeset: https://hg.mozilla.org/mozilla-central/rev/0123456789ab"
    )
    assert not _is_landing(
        "The misalignment is caused by bug 1910880, first bad commit is\n"
        "https://hg.mozilla.org/mozilla-central/rev/0123456789ab"
    )


def test_uninformative_signatures_are_not_searched():
    assert _informative("[@ mozilla::EditorSpellCheck::SetCurrentDictionaries]")
    for signature in ("[@ ??]", "[@ @0x0]", "[@ EMPTY: no crashing thread identified]"):
        assert not _informative(signature), signature


def test_distinctive_summary_words():
    state = CrashInformationState(
        bug_id=1971519,
        taxonomy_version="5",
        report_snapshot=ReportSnapshot(
            bug_id=1971519,
            url="https://bugzilla.mozilla.org/show_bug.cgi?id=1971519",
            summary="Crash [@ ??] with --disable-main-thread-denormals on ARM64",
        ),
    )
    assert PriorBugs(state, sources=None)._distinctive() == [
        "disable-main-thread-denormals",
        "ARM64",
    ]


def test_earlier_bugs_are_also_searched_by_class():
    state = CrashInformationState(
        bug_id=2042439,
        taxonomy_version="5",
        report_snapshot=ReportSnapshot(
            bug_id=2042439,
            url="https://bugzilla.mozilla.org/show_bug.cgi?id=2042439",
            summary="Content process crashes in ActiveScrolledRoot::AssertDepthInvariant",
            crash_signatures=["mozilla::ActiveScrolledRoot::AssertDepthInvariant"],
        ),
    )
    retriever = PriorBugs(state, sources=None)
    assert retriever._classes() == ["ActiveScrolledRoot"]
    assert {"summary": "ActiveScrolledRoot"} in retriever._searches(None)


def test_earlier_bugs_are_told_without_noise():
    frames = "\n".join(f"{i}  xul.dll  frame{i}  a.cpp:{i}" for i in range(10))
    case = PriorCase(
        bug_id=1808177,
        summary="Crash in [@ nsINode::OwnerDoc]",
        product="Core",
        component="DOM: File",
        signatures=["[@ nsINode::OwnerDoc]"],
        created=datetime(2023, 1, 2, tzinfo=UTC),
        fixed=datetime(2023, 1, 4, tzinfo=UTC),
        report=f"Crash report.\n\nTop 10 frames of crashing thread:\n```\n{frames}\n```",
        discussion=[
            "The bug is linked to a topcrash signature, which matches the criteria. "
            "See the auto_nag documentation.",
            "Created attachment 9310479 Bug 1808177 - Check the container. r?masayuki",
            "mPointToInsert can be null after the reader is cancelled.",
        ],
        fix_title="mPointToInsert.GetContainer() could be nullptr",
        fix={
            "node": "96e1597780facb27a866",
            "pushdate": "2023-01-04",
            "desc": "Bug 1808177 - mPointToInsert.GetContainer() could be nullptr",
            "files": ["a.cpp"],
            "diff": "# HG changeset patch\n# User Someone\nBug 1808177 - fix\n\n"
            "Differential Revision: https://phabricator.services.mozilla.com/D1\n\n"
            "diff --git a/a.cpp b/a.cpp\n+new",
        },
    )
    text = case.text()
    assert "frame4" in text and "frame5" not in text
    assert "topcrash" not in text and "Created attachment" not in text
    assert "- mPointToInsert can be null after the reader is cancelled." in text
    assert "# HG changeset patch" not in text and "Differential Revision" not in text
    assert "diff --git a/a.cpp b/a.cpp\n+new" in text
