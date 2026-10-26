"""Tests for the post-run check on the other bugs a run's writes name."""

import asyncio

import pytest
from conftest import DUPE, _actions, _target, fake_broker
from hackbot_agents.gecko_triage import targets
from hackbot_agents.gecko_triage.config import TEAM_META_BUGS, TRIAGE_SCOPE
from hackbot_agents.gecko_triage.targets import (
    TARGET_FIELDS,
    CommentNamesRestricted,
    Dropped,
    DupeTargetIsMeta,
    DupeTargetRestricted,
    bugs_to_read,
    comment_bug_ids,
    drop_writes,
    dupe_of,
    dupe_target_warning,
    fetch_bugs,
    restricted_warning,
    screen_comment,
    screen_writes,
)
from hackbot_runtime.actions import ActionsRecorder

BUG = 9000001
REGRESSOR = 9000003
META = sorted(TEAM_META_BUGS)[0]
SCOPE = TRIAGE_SCOPE[0]


def _regression(regressed_by, **changes):
    return [
        {
            "action_id": "u",
            "type": "bugzilla.update_bug",
            "params": {
                "bug_id": BUG,
                "changes": {**changes, "regressed_by": {"add": regressed_by}},
            },
        }
    ]


def test_the_bug_to_check_is_dupe_of():
    assert dupe_of(_actions(), BUG) == DUPE
    assert dupe_of(_actions()[:1], BUG) is None


def test_one_fetch_reads_the_dupe_target_and_the_regressors():
    assert bugs_to_read(_actions(regressed_by=[REGRESSOR, DUPE]), BUG) == [
        DUPE,
        REGRESSOR,
    ]
    assert bugs_to_read(_regression([REGRESSOR, "junk"]), BUG) == [REGRESSOR]
    assert bugs_to_read(_actions()[:1], BUG) == []


def test_the_fetch_asks_for_the_fields_the_check_reads():
    for name in ("groups", "component", "product", "status", "resolution", "keywords"):
        assert name in TARGET_FIELDS.split(","), name


def test_a_public_open_team_target_keeps_the_meta_write():
    assert screen_writes(_actions(), BUG, {DUPE: _target()}) == []


@pytest.mark.parametrize(
    ("target", "why"),
    [
        (_target(status="RESOLVED", resolution="FIXED"), "closed"),
        (_target(product="Firefox", component="General"), "out of the team's scope"),
        (_target(keywords=["sec-high"]), "security"),
    ],
)
def test_the_meta_write_is_dropped_on_an_unsuitable_target(target, why):
    dropped = screen_writes(_actions(), BUG, {DUPE: target})
    assert [(d.action_id, d.field) for d in dropped] == [("m", None)]
    assert why in dropped[0].reason


@pytest.mark.parametrize(
    "target", [_target(keywords=["meta"]), _target(summary="[meta] Media tracking")]
)
def test_a_duplicate_of_a_meta_bug_fails_the_run(target):
    for actions in (_actions(), _actions(meta_on=None)):
        with pytest.raises(DupeTargetIsMeta, match=f"bug {DUPE}"):
            screen_writes(actions, BUG, {DUPE: target})


def test_a_write_on_a_bug_other_than_dupe_of_is_dropped():
    dropped = screen_writes(_actions(meta_on=DUPE + 1), BUG, {DUPE: _target()})
    assert [d.action_id for d in dropped] == ["m"]
    assert "duplicate" in dropped[0].reason


def test_a_write_on_another_bug_without_its_duplicate_change_is_dropped():
    # The model can remove the DUPLICATE change after the meta write passed its hook.
    actions = [a for a in _actions() if a["action_id"] != "u"]
    dropped = screen_writes(actions, BUG, {})
    assert [d.action_id for d in dropped] == ["m"]
    assert "duplicate" in dropped[0].reason


@pytest.mark.parametrize("found", [{DUPE: _target(groups=["core-security"])}, {}])
def test_a_restricted_or_unreadable_dupe_of_fails_the_run(found):
    with pytest.raises(DupeTargetRestricted):
        screen_writes(_actions(meta_on=None), BUG, found)


def test_a_readable_regressor_keeps_the_regressed_by_write():
    closed = _target(id=REGRESSOR, status="RESOLVED", resolution="FIXED")
    assert screen_writes(_regression([REGRESSOR]), BUG, {REGRESSOR: closed}) == []


@pytest.mark.parametrize(
    "found", [{}, {REGRESSOR: _target(id=REGRESSOR, groups=["media-core-security"])}]
)
def test_an_unreadable_or_restricted_regressor_drops_the_regressed_by_write(found):
    dropped = screen_writes(_regression([REGRESSOR]), BUG, found)
    assert [(d.action_id, d.field) for d in dropped] == [("u", "regressed_by")]
    assert f"bug {REGRESSOR}" in dropped[0].reason


RELATED = 9000004


def _related(see_also, **changes):
    return [
        {
            "action_id": "u",
            "type": "bugzilla.update_bug",
            "params": {
                "bug_id": BUG,
                "changes": {**changes, "see_also": {"add": see_also}},
            },
        }
    ]


def test_one_fetch_also_reads_the_see_also_bugs():
    actions = _related([RELATED, "junk"], regressed_by={"add": [REGRESSOR]})
    assert bugs_to_read(actions, BUG) == [REGRESSOR, RELATED]


def test_a_readable_see_also_bug_keeps_the_see_also_write():
    closed = _target(id=RELATED, status="RESOLVED", resolution="FIXED")
    assert screen_writes(_related([RELATED]), BUG, {RELATED: closed}) == []


@pytest.mark.parametrize(
    "found", [{}, {RELATED: _target(id=RELATED, groups=["media-core-security"])}]
)
def test_an_unreadable_or_restricted_see_also_bug_drops_the_see_also_write(found):
    dropped = screen_writes(_related([RELATED], severity="S3"), BUG, found)
    assert [(d.action_id, d.field) for d in dropped] == [("u", "see_also")]
    assert f"bug {RELATED}" in dropped[0].reason


def test_a_meta_bug_in_see_also_drops_the_see_also_write():
    meta = _target(id=RELATED, keywords=["meta"])
    dropped = screen_writes(_related([RELATED], severity="S3"), BUG, {RELATED: meta})
    assert [(d.action_id, d.field) for d in dropped] == [("u", "see_also")]
    assert "meta bug" in dropped[0].reason


def test_a_meta_regressor_keeps_the_regressed_by_write():
    meta = _target(id=REGRESSOR, keywords=["meta"])
    assert screen_writes(_regression([REGRESSOR]), BUG, {REGRESSOR: meta}) == []


def test_each_unreadable_field_is_dropped_on_its_own():
    actions = _related([RELATED], regressed_by={"add": [REGRESSOR]})
    dropped = screen_writes(actions, BUG, {})
    assert sorted(d.field for d in dropped) == ["regressed_by", "see_also"]


def test_dropping_a_field_keeps_the_rest_of_the_change():
    recorder = ActionsRecorder()
    kept = recorder.record(
        "bugzilla.update_bug",
        {
            "bug_id": BUG,
            "changes": {"severity": "S3", "regressed_by": {"add": [REGRESSOR]}},
        },
        reasoning="r",
    )
    alone = recorder.record(
        "bugzilla.update_bug",
        {"bug_id": DUPE, "changes": {"regressed_by": {"add": [REGRESSOR]}}},
        reasoning="r",
    )
    drop_writes(
        recorder,
        [
            Dropped(kept["action_id"], "x", "regressed_by"),
            Dropped(alone["action_id"], "x", "regressed_by"),
        ],
    )
    assert [a["params"]["changes"] for a in recorder.actions] == [{"severity": "S3"}]


def test_dropping_an_action_removes_it():
    recorder = ActionsRecorder()
    action = recorder.record(
        "bugzilla.update_bug", {"bug_id": DUPE, "changes": {}}, reasoning="r"
    )
    drop_writes(recorder, [Dropped(action["action_id"], "x")])
    assert recorder.actions == []


async def test_the_fetch_never_raises(monkeypatch):
    fake_broker(monkeypatch, {"get_bugs": OSError("connection refused")})
    assert await fetch_bugs({"type": "http", "url": ""}, [1]) is None
    assert await fetch_bugs({"type": "http", "url": "http://b/mcp"}, [1]) is None
    assert await fetch_bugs({"type": "http", "url": ""}, []) == {}


async def test_the_fetch_gives_up_after_the_timeout(monkeypatch):
    async def hang(args):
        await asyncio.sleep(10)

    fake_broker(monkeypatch, {"get_bugs": hang})
    monkeypatch.setattr(targets, "FETCH_TIMEOUT", 0.01)
    assert await fetch_bugs({"type": "http", "url": "http://b/mcp"}, [1]) is None


@pytest.mark.parametrize("reply", [[], None, "junk"])
async def test_an_odd_reply_gives_no_bugs_and_no_error(monkeypatch, reply):
    fake_broker(monkeypatch, {"get_bugs": reply})
    assert await fetch_bugs({"type": "http", "url": "http://b/mcp"}, [1]) is None


async def test_the_fetch_reads_the_bugs(monkeypatch):
    calls = fake_broker(
        monkeypatch, {"get_bugs": {"bugs": [_target(), {"id": "junk"}, "junk"]}}
    )
    assert await fetch_bugs({"type": "http", "url": "http://b/mcp"}, [DUPE, 1]) == {
        DUPE: _target()
    }
    assert calls == [
        ("connect", "http://b/mcp"),
        ("get_bugs", {"ids": [DUPE, 1], "include_fields": TARGET_FIELDS}),
    ]


def _commented(text):
    return [
        {
            "action_id": "c",
            "type": "bugzilla.add_comment",
            "params": {"bug_id": BUG, "text": text},
        }
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Bug 1234567 caused it.", {1234567}),
        ("bug #1234567", {1234567}),
        ("bugs 1234567 and 7654321", {1234567, 7654321}),
        ("bugs 1234567, 765432 or 1111111.", {1234567, 765432, 1111111}),
        ("bugs 1234567, 765432, and 1111111.", {1234567, 765432, 1111111}),
        ("bug-1234567", {1234567}),
        ("bug1234567", {1234567}),
        ("bmo#1234567", {1234567}),
        ("https://bugzil.la/1234567", {1234567}),
        ("https://bugzilla.mozilla.org/1234567", {1234567}),
        ("https://bugzilla.mozilla.org/show_bug.cgi?id=1234567#c3", {1234567}),
        ("https://bugzilla.mozilla.org/show_bug.cgi?format=x&id=1234567", {1234567}),
        (f"bug {BUG} only", set()),
        ("A bare 1234567 here.", set()),
        ("192000 Hz, 1500000 bps, 1920x1080 and 2073600 pixels at 123456.789 s", set()),
        ("attachment 9512345 and attachment.cgi?id=9512346", set()),
    ],
)
def test_an_explicit_bug_reference_in_a_comment_is_a_bug_to_read(text, expected):
    assert comment_bug_ids(_commented(text), BUG) == expected
    assert bugs_to_read(_commented(text), BUG) == sorted(expected)


def test_the_read_also_takes_the_commit_message_bugs():
    assert bugs_to_read(_commented("x"), BUG, {REGRESSOR, BUG}) == [REGRESSOR]


def test_a_comment_that_names_a_bug_fails_when_the_read_failed():
    with pytest.raises(
        CommentNamesRestricted,
        match="the broker read for the bugs this comment names failed or is not "
        "configured",
    ):
        screen_comment(_commented(f"Bug {REGRESSOR} caused it."), BUG, None)
    screen_comment(_commented("A recent change caused it."), BUG, None)


@pytest.mark.parametrize(
    "found", [{}, {REGRESSOR: _target(id=REGRESSOR, groups=["media-core-security"])}]
)
def test_a_comment_that_names_a_restricted_bug_fails(found):
    with pytest.raises(CommentNamesRestricted, match=f"bug {REGRESSOR}"):
        screen_comment(_commented(f"Bug {REGRESSOR} caused it."), BUG, found)


def test_a_comment_that_names_readable_bugs_or_none_passes():
    found = {REGRESSOR: _target(id=REGRESSOR)}
    screen_comment(_commented(f"Bug {REGRESSOR} caused it."), BUG, found)
    screen_comment(_commented("A recent change caused it."), BUG, {})
    screen_comment(_commented(f"This bug {BUG} only."), BUG, {})


HIDDEN = 1234567


def test_a_comment_with_no_bug_reference_passes():
    screen_comment(_commented("It plays at 192000 Hz."), BUG, {}, commit_bugs={HIDDEN})


@pytest.mark.parametrize(
    "text",
    [
        "The cause is 1234567.",
        "See bmo1234567.",
        "See regressor1234567 now.",
        "It is 1234 567.",
        "It is 1,234,567.",
        "It is 1-234-567.",
    ],
)
@pytest.mark.parametrize(
    "found", [{}, None, {HIDDEN: _target(id=HIDDEN, groups=["x"])}]
)
def test_a_restricted_commit_bug_number_fails_in_every_spelling(text, found):
    with pytest.raises(CommentNamesRestricted, match=f"bug {HIDDEN}|broker read"):
        screen_comment(_commented(text), BUG, found, commit_bugs={HIDDEN})


@pytest.mark.parametrize(
    "text", ["It is 11234567.", "It is 12345678.", "It is 1234 5678.", "x 1234a567"]
)
def test_a_longer_number_is_not_the_commit_bug(text):
    screen_comment(_commented(text), BUG, {}, commit_bugs={HIDDEN})


def test_a_readable_commit_bug_may_be_named():
    found = {HIDDEN: _target(id=HIDDEN)}
    screen_comment(
        _commented("Bug 1234567 is related."), BUG, found, commit_bugs={HIDDEN}
    )


def test_a_failed_read_drops_no_link_change():
    actions = _related([RELATED], regressed_by={"add": [REGRESSOR]})
    assert screen_writes(actions, BUG, None) == []
    assert sorted(d.field for d in screen_writes(actions, BUG, {})) == [
        "regressed_by",
        "see_also",
    ]


PERSON_CHECK = (
    "a person must check that the comment does not name or describe these bugs"
)


def test_the_restricted_warning_lists_the_bugs_the_run_met():
    found = {
        REGRESSOR: _target(id=REGRESSOR),
        RELATED: _target(id=RELATED, groups=["x"]),
    }
    assert restricted_warning([REGRESSOR], found) is None
    assert restricted_warning([], None) is None
    warning = restricted_warning([REGRESSOR, RELATED, HIDDEN], found)
    assert (
        f"bug {RELATED}, bug {HIDDEN}" in warning and f"bug {REGRESSOR}," not in warning
    )
    assert PERSON_CHECK in warning
    assert f"bug {REGRESSOR}, bug {RELATED}" in restricted_warning(
        [REGRESSOR, RELATED], None
    )


@pytest.mark.parametrize(
    ("status", "resolution"),
    [
        ("RESOLVED", "INVALID"),
        ("RESOLVED", "WONTFIX"),
        ("RESOLVED", "WORKSFORME"),
        ("RESOLVED", "INCOMPLETE"),
        ("RESOLVED", "DUPLICATE"),
        ("VERIFIED", "INVALID"),
        ("CLOSED", "WONTFIX"),
    ],
)
def test_a_duplicate_of_a_target_closed_with_no_fix_needs_a_person(status, resolution):
    found = {DUPE: _target(status=status, resolution=resolution)}
    warning = dupe_target_warning(_actions(meta_on=None), BUG, found)
    assert f"bug {DUPE} is {status} {resolution}".strip() + ", not FIXED" in warning


@pytest.mark.parametrize(
    ("status", "resolution"),
    [
        ("RESOLVED", "FIXED"),
        ("VERIFIED", "FIXED"),
        ("NEW", ""),
        ("ASSIGNED", ""),
        ("UNCONFIRMED", ""),
        ("REOPENED", ""),
    ],
)
def test_a_duplicate_of_a_fixed_or_open_target_needs_no_person(status, resolution):
    found = {DUPE: _target(status=status, resolution=resolution)}
    assert dupe_target_warning(_actions(meta_on=None), BUG, found) is None


def test_a_run_with_no_duplicate_has_no_dupe_target_warning():
    actions = _regression([REGRESSOR], severity="S3")
    assert dupe_target_warning(actions, BUG, {}) is None


# --- the bugs the broker withheld during the run ---------------------------- #


@pytest.mark.parametrize("text", ["The change 1234567 caused it.", "It is 1,234,567."])
@pytest.mark.parametrize("found", [{}, {HIDDEN: _target(id=HIDDEN)}])
def test_a_comment_with_a_withheld_bug_number_fails(text, found):
    with pytest.raises(CommentNamesRestricted, match="withheld from a read"):
        screen_comment(_commented(text), BUG, found, withheld={HIDDEN})


def test_a_comment_with_no_withheld_number_passes():
    screen_comment(_commented("A recent change caused it."), BUG, {}, withheld={HIDDEN})
    screen_comment(_commented(f"This bug {BUG} only."), BUG, {}, withheld={BUG})


UNCHECKED = "a get_bugs result was saved to a file and could not be checked"


def test_an_unchecked_read_holds_the_run_with_its_reason():
    warning = restricted_warning([], {}, UNCHECKED)
    assert warning == (
        f"{UNCHECKED}; a person must check that the comment names or describes no "
        "restricted bug"
    )
    both = restricted_warning([HIDDEN], {}, UNCHECKED)
    assert f"bug {HIDDEN}" in both and UNCHECKED in both


def test_no_hidden_bug_and_no_unchecked_read_is_no_warning():
    assert restricted_warning([], {}) is None
    assert restricted_warning([HIDDEN], {HIDDEN: _target(id=HIDDEN)}, None) is None
