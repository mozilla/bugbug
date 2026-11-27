"""Taxonomy, report parsing, and the triage and grounding checks, unit by unit."""

from hackbot_agents.crash_info.evidence import EvidenceStore, resolve_ids
from hackbot_agents.crash_info.extraction import extract_indicators, parse_frame
from hackbot_agents.crash_info.knowledge import code_identifiers
from hackbot_agents.crash_info.models import (
    CrashInformationState,
    EvidenceSource,
    Provenance,
    ReportSnapshot,
    StackFrame,
)
from hackbot_agents.crash_info.react_tools import (
    crash_anchors,
    invented_identifiers,
    is_specific,
)
from hackbot_agents.crash_info.recovery import (
    contains_queries,
    crash_stats_notes,
    stack_frames_in,
    usable_stack,
)
from hackbot_agents.crash_info.taxonomy import (
    load_taxonomy,
    plain_name,
    with_plain_names,
)
from hackbot_agents.crash_info.textutil import jaccard

TAXONOMY = load_taxonomy()


# --- Taxonomy ---------------------------------------------------------------------


def test_taxonomy_matches_the_document():
    assert len(TAXONOMY.labels) == 13
    assert [c.id for c in TAXONOMY.categories] == [
        "Repair",
        "Obj",
        "Exec",
        "Verif",
        "Trigger",
    ]
    assert [label.id for label in TAXONOMY.in_category("Repair")] == [
        "Repair.GUARD",
        "Repair.ERROR",
        "Repair.STOP",
        "Repair.OWNERSHIP",
    ]
    assert (
        TAXONOMY.label("Obj.LIFETIME").canonical_question
        == "What keeps this object or resource valid, and until when?"
    )
    assert [label.id for label in TAXONOMY.labels if label.phase == "late"] == [
        "Repair.OWNERSHIP"
    ]
    assert all(
        label.card_line().startswith(f"- {label.id} (") for label in TAXONOMY.labels
    )


def test_each_label_has_a_plain_name_for_people():
    plain = [label.plain for label in TAXONOMY.labels]
    assert all(plain) and len(set(plain)) == 13
    assert plain_name("Repair.GUARD") == "Which cases the fix must cover"
    assert plain_name("Nope.X") == "Nope.X" and plain_name(None) == "Unlabelled"
    # The note's tags, named for people; unknown tags left as they are.
    assert with_plain_names("1. [Obj.LIFETIME] Until when?\n- [Foo.BAR] x") == (
        "1. **How long the object stays valid**: Until when?\n- [Foo.BAR] x"
    )
    # Prompts keep the codes.
    assert "How long" not in TAXONOMY.label("Obj.LIFETIME").card_line()


def test_label_spellings_normalize():
    for raw in (
        "obj.state",
        "OBJ_STATE",
        "Object state & identity",
        "Obj.STATE (Object state & identity)",
    ):
        assert TAXONOMY.normalize(raw) == "Obj.STATE"
    assert TAXONOMY.normalize("something else") is None
    lifetime = TAXONOMY.label("Obj.LIFETIME")
    assert lifetime.nearby == ("Obj.STATE", "Exec.CONCURRENCY")
    assert "Freeze time" in lifetime.full_text()


# --- Extraction -------------------------------------------------------------------


def test_frames_in_the_formats_bugs_paste():
    frame = parse_frame("0  xul.dll  nsINode::OwnerDoc() const  dom/base/nsINode.h:793")
    assert (frame.index, frame.module, frame.function, frame.file, frame.line) == (
        0,
        "xul.dll",
        "nsINode::OwnerDoc",
        "dom/base/nsINode.h",
        793,
    )
    frame = parse_frame(
        "2 | XUL | mozilla::Foo::Run(int) | hg:hg.mozilla.org/mozilla-central:dom/foo/Foo.cpp:abc123:55"
    )
    assert frame.function == "mozilla::Foo::Run" and frame.file == "dom/foo/Foo.cpp"
    frame = parse_frame(
        "1  xul.dll  XPTC__InvokebyIndex()  /builds/worker/checkouts/gecko/xpcom/reflect/xptcall/md/win32/xptcinvoke_asm_x86_64.asm:97"
    )
    assert frame.file == "xpcom/reflect/xptcall/md/win32/xptcinvoke_asm_x86_64.asm"
    assert parse_frame("Reason: EXCEPTION_ACCESS_VIOLATION_READ") is None


def test_indicators():
    text = (
        "See bug 1234567 and bp-aaaaaaaa-bbbb-cccc-dddd-eeeeee260101.\n"
        "MOZ_RELEASE_ASSERT(mFoo) in `mWidget` at dom/foo/Bar.cpp:12 via mozilla::dom::Bar::Run"
    )
    ind = extract_indicators([text], bug_id=1)
    assert ind.bug_refs == [1234567]
    assert ind.crash_ids == ["aaaaaaaa-bbbb-cccc-dddd-eeeeee260101"]
    assert ind.crash_reasons == ["MOZ_RELEASE_ASSERT(mFoo)"]
    assert "dom/foo/Bar.cpp" in ind.files
    assert {"mozilla::dom::Bar::Run", "mWidget"} <= set(ind.identifiers)


def test_resolve_ids_accepts_loose_spellings():
    known = {"ev-003", "fact-002"}
    assert resolve_ids(["[ev-003]", "EV-3", "3", "fact 2", "ev-999"], known) == [
        "ev-003",
        "fact-002",
    ]


# --- Triage and grounding checks ----------------------------------------------


def make_state() -> CrashInformationState:
    state = CrashInformationState(bug_id=1, taxonomy_version="5")
    state.report_snapshot = ReportSnapshot(
        bug_id=1,
        url="u",
        summary="Crash in [@ mozilla::Foo::Bar]",
        product="Core",
        component="DOM: Core",
        crash_signatures=["mozilla::Foo::Bar"],
    )
    state.indicators.stack_frames = [
        StackFrame(index=0, function="RefPtr<Foo>::get", file="mfbt/RefPtr.h", line=3),
        StackFrame(
            index=1, function="mozilla::Foo::Bar", file="dom/foo/Foo.cpp", line=42
        ),
    ]
    state.indicators.identifiers = ["mWidget"]
    state.indicators.files = ["dom/foo/Foo.cpp"]
    return state


def test_specificity_and_duplicates():
    anchors = crash_anchors(make_state())
    assert is_specific("What keeps mWidget alive?", anchors)
    assert is_specific("Is Foo::Bar called during shutdown?", anchors)
    assert not is_specific(
        "What observable result would show the repair is correct?", anchors
    )
    assert (
        jaccard(
            "What is the condition or state that makes mWidget null?",
            "What are the specific conditions or states that make mWidget null, leading to crashes?",
        )
        >= 0.6
    )


def test_names_found_in_no_evidence():
    state = make_state()
    EvidenceStore(state).add(
        "code",
        "dom/foo/Foo.cpp lines 40-44",
        "void Foo::Bar() { mWidget->Paint(); }",
        Provenance(source=EvidenceSource.SEARCHFOX, locator="dom/foo/Foo.cpp:42"),
    )
    answer = "Foo::Bar calls mWidget->Paint() after nsWindow::Destroy cleared it."
    assert invented_identifiers(state, answer) == ["nsWindow::Destroy"]


def test_code_identifiers():
    assert code_identifiers(
        "Is mWidget freed by nsWindow::Destroy before JavaScript runs?"
    ) == ["mWidget", "nsWindow::Destroy"]


# --- Recovery helpers ------------------------------------------------------------


def test_crash_stats_notes():
    notes = crash_stats_notes(
        100,
        {
            "address": [("0x0", 90), ("0x7ffe0000", 10)],
            "product": [("Thunderbird", 98)],
            "startup_crash": [("F", 100)],
        },
    )
    assert notes[0].startswith("90 of 100 (90%) crashes are at a near-null address")
    assert "98 of 100 (98%) crashes are in product 'Thunderbird'" in notes
    assert "none are startup crashes" in notes


def test_a_signature_no_crash_has_is_searched_by_its_specific_frames():
    assert contains_queries(
        "mozilla::detail::InvalidArrayIndex_CRASH | nsTArray_Impl<T>::ElementAt "
        "| mozilla::net::DictionaryOrigin::SetCacheEntry"
    ) == ["~mozilla::net::DictionaryOrigin::SetCacheEntry"]
    assert contains_queries(
        "libc.so | mozilla::RecursiveMutex::LockInternal | a::B::C | d::E::F"
    ) == ["~mozilla::RecursiveMutex::LockInternal", "~a::B::C"]
    # Nothing more specific than the signature itself, or no code name at all.
    assert contains_queries("mozilla::dom::Foo::Bar") == []
    assert contains_queries("notify_register_check") == []


def test_a_usable_stack_names_its_functions():
    named = ["0 xul.dll mozilla::Foo::Bar() (dom/foo/Foo.cpp:42)", "1 xul.dll Baz"]
    assert usable_stack(named)
    assert not usable_stack(["0 xul.dll 0x1a2b", "1 libc.so ?", "2 xul.dll Baz"])
    content = "reason: SIGSEGV\nCrashing thread frames:\n" + "\n".join(named)
    assert stack_frames_in(content) == named and stack_frames_in("reason: x") == []
