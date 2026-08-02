"""Tests for the feedback endpoints.

Exercises the handlers directly with a fake DB, matching this suite's style.
The negative cases carry the weight: a GET must never write, every rejection
must look identical from outside, and a missing or stale nonce must stop the
write.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from app import feedback_links
from app.config import settings
from app.routers import feedback as feedback_router
from app.schemas import (
    FeedbackCreate,
    FeedbackDimension,
    FeedbackRating,
    InternalFeedbackCreate,
    RunStatus,
)
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError


@dataclass
class _FakeRun:
    status: str = RunStatus.succeeded.value
    agent: str = "frontend-triage"
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)


class _FakeAction:
    def __init__(self, bug_id=42, text="Root cause: the pref is never read."):
        self.params = {"bug_id": bug_id, "text": text}


class _FakeDB:
    """Minimal stand-in: `get` returns the run, `execute`/`scalar` are scripted."""

    def __init__(self, run=None, action=None, other_votes=0):
        self._run = run
        self._action = action
        self._other_votes = other_votes
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.updates = 0

    async def get(self, model, run_id):
        return self._run

    async def execute(self, stmt):
        # The only non-select statement the handler issues is the upsert UPDATE.
        if stmt.is_update:
            self.updates += 1
            return None
        return _FakeResult(self._action)

    async def scalar(self, stmt):
        return self._other_votes

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _FakeResult:
    def __init__(self, action):
        self._action = action

    def scalars(self):
        return self

    def first(self):
        return self._action


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "feedback_token_secret", "test-feedback-secret")
    monkeypatch.setattr(settings, "feedback_public_base_url", "https://example.test")
    monkeypatch.setattr(settings, "feedback_nonce_ttl_seconds", 3600)
    monkeypatch.setattr(settings, "feedback_max_anonymous_votes", 50)


def _payload(run_id, **overrides):
    body = {
        "rating": FeedbackRating.up,
        "nonce": feedback_links.mint_nonce(run_id),
        "dimensions": [],
        "comment": None,
    }
    body.update(overrides)
    return FeedbackCreate(**body)


# --- resolving the target --------------------------------------------- #


async def test_get_returns_the_posted_comment_and_a_nonce():
    run = _FakeRun()
    db = _FakeDB(run=run, action=_FakeAction())

    out = await feedback_router.get_feedback_target(
        feedback_links.mint_token(run.run_id), db
    )

    assert out.bug_id == 42
    assert out.comment == "Root cause: the pref is never read."
    assert feedback_links.verify_nonce(run.run_id, out.nonce) is True


async def test_get_trims_the_bugzilla_footer_from_the_preview():
    run = _FakeRun()
    body = (
        "Root cause: the pref is never read.\n\n"
        "*This is an automated analysis result. If this result is incorrect "
        "please add a needinfo and feel free to correct the error.* "
    )
    db = _FakeDB(run=run, action=_FakeAction(text=body))

    out = await feedback_router.get_feedback_target(
        feedback_links.mint_token(run.run_id), db
    )

    assert out.comment == "Root cause: the pref is never read."


async def test_get_never_writes():
    """Automated clients follow this link; none of them may vote."""
    run = _FakeRun()
    db = _FakeDB(run=run, action=_FakeAction())

    for _ in range(5):
        await feedback_router.get_feedback_target(
            feedback_links.mint_token(run.run_id), db
        )

    assert db.added == []
    assert db.commits == 0
    assert db.updates == 0


async def test_bad_token_404s():
    """Token shapes are covered in test_feedback_links; this is the route's guard."""
    run = _FakeRun()
    with pytest.raises(HTTPException) as exc:
        await feedback_router.get_feedback_target(
            f"{run.run_id.hex}.{'0' * 32}", _FakeDB(run=run)
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"


async def test_unknown_run_404s_identically():
    with pytest.raises(HTTPException) as exc:
        await feedback_router.get_feedback_target(
            feedback_links.mint_token(uuid.uuid4()), _FakeDB(run=None)
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Not found"


async def test_a_run_that_did_not_succeed_404s():
    run = _FakeRun(status=RunStatus.failed.value)
    with pytest.raises(HTTPException) as exc:
        await feedback_router.get_feedback_target(
            feedback_links.mint_token(run.run_id),
            _FakeDB(run=run, action=_FakeAction()),
        )
    assert exc.value.status_code == 404


# --- recording a vote --------------------------------------------------- #


async def test_post_records_the_vote():
    run = _FakeRun()
    db = _FakeDB(run=run, action=_FakeAction())

    out = await feedback_router.submit_feedback(
        feedback_links.mint_token(run.run_id),
        _payload(
            run.run_id,
            rating=FeedbackRating.down,
            dimensions=[FeedbackDimension.root_cause_wrong],
            comment="The regressor is bug 123, not bug 456.",
        ),
        db,
        x_rater_key="cookie-a",
        x_forwarded_for="203.0.113.7, 70.41.3.18",
        user_agent="Firefox",
    )

    assert "Thank you" in out.message
    assert db.commits == 1
    (row,) = db.added
    assert row.run_id == run.run_id
    assert row.rating == "down"
    assert row.dimensions == ["root_cause_wrong"]
    assert row.comment == "The regressor is bug 123, not bug 456."
    assert row.rater_kind == "anonymous"
    assert row.anon_id == feedback_links.anon_id("cookie-a", None, None)


async def test_post_without_a_nonce_is_rejected():
    run = _FakeRun()
    db = _FakeDB(run=run, action=_FakeAction())

    with pytest.raises(HTTPException) as exc:
        await feedback_router.submit_feedback(
            feedback_links.mint_token(run.run_id),
            _payload(run.run_id, nonce="not-a-nonce"),
            db,
        )

    assert exc.value.status_code == 400
    assert db.added == []
    assert db.commits == 0


async def test_anonymous_vote_cap_rejects_new_raters():
    run = _FakeRun()
    db = _FakeDB(run=run, action=_FakeAction(), other_votes=50)

    with pytest.raises(HTTPException) as exc:
        await feedback_router.submit_feedback(
            feedback_links.mint_token(run.run_id), _payload(run.run_id), db
        )

    assert exc.value.status_code == 429
    assert db.added == []


async def test_cap_does_not_count_the_rater_changing_their_own_mind():
    run = _FakeRun()
    db = _FakeDB(run=run, action=_FakeAction(), other_votes=49)

    await feedback_router.submit_feedback(
        feedback_links.mint_token(run.run_id), _payload(run.run_id), db
    )

    assert db.commits == 1


# --- rating from Hackbot, before the comment is posted ------------------ #


async def test_a_reviewer_can_rate_a_comment_that_was_never_applied():
    """The point of the internal route: reject an analysis, don't post it."""
    run = _FakeRun()
    db = _FakeDB(run=run, action=_FakeAction())

    out = await feedback_router.submit_internal_feedback(
        run.run_id,
        InternalFeedbackCreate(
            rating=FeedbackRating.down,
            dimensions=[FeedbackDimension.should_not_have_commented],
            comment="Not worth posting.",
        ),
        db,
        x_on_behalf_of="mconley@mozilla.com",
    )

    assert "Thank you" in out.message
    (row,) = db.added
    assert row.rater_kind == "mozilla"
    assert row.rater_id == "mconley@mozilla.com"
    assert row.anon_id is None
    assert row.rating == "down"


async def test_the_internal_route_does_not_require_an_applied_comment():
    """Contrast with the public route, which 404s without one."""
    run = _FakeRun()
    await feedback_router._comment_action(
        _FakeDB(run=run, action=_FakeAction()), run.run_id, applied_only=False
    )

    with pytest.raises(HTTPException):
        await feedback_router._comment_action(
            _FakeDB(run=run, action=None), run.run_id, applied_only=True
        )


async def test_an_agent_that_has_not_opted_in_cannot_be_rated():
    """One registry flag governs both surfaces; bug-fix has not set it."""
    run = _FakeRun(agent="bug-fix")
    db = _FakeDB(run=run, action=_FakeAction())

    with pytest.raises(HTTPException) as exc:
        await feedback_router.submit_internal_feedback(
            run.run_id,
            InternalFeedbackCreate(rating=FeedbackRating.up),
            db,
            x_on_behalf_of="mconley@mozilla.com",
        )

    assert exc.value.status_code == 404
    assert db.added == []


async def test_internal_feedback_needs_an_identity():
    """Identity comes from the session; without it there is nothing to dedupe on."""
    run = _FakeRun()
    db = _FakeDB(run=run, action=_FakeAction())

    with pytest.raises(HTTPException) as exc:
        await feedback_router.submit_internal_feedback(
            run.run_id,
            InternalFeedbackCreate(rating=FeedbackRating.up),
            db,
            x_on_behalf_of=None,
        )

    assert exc.value.status_code == 400
    assert db.added == []


class _ListDB:
    """Returns scripted (RunFeedback-ish, agent, inputs) tuples for the join."""

    def __init__(self, rows):
        self._rows = rows
        self.stmt = None

    async def execute(self, stmt):
        self.stmt = stmt
        return SimpleNamespace(all=lambda: self._rows)


async def test_list_feedback_joins_agent_and_bug_from_the_run():
    run_id = uuid.uuid4()
    row = SimpleNamespace(
        run_id=run_id,
        rating="down",
        dimensions=["root_cause_wrong"],
        comment="Wrong regressor.",
        rater_kind="mozilla",
        rater_id="mconley@mozilla.com",
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    db = _ListDB([(row, "frontend-triage", {"bug_id": 2049877})])

    (out,) = await feedback_router.list_feedback(db)

    assert out.run_id == run_id
    assert out.agent == "frontend-triage"
    assert out.bug_id == 2049877
    assert out.rating == FeedbackRating.down
    assert out.comment == "Wrong regressor."
    assert out.rater_id == "mconley@mozilla.com"


async def test_dimension_filter_reaches_the_query():
    """A silently dropped filter would look like "no rows match" instead."""
    db = _ListDB([])
    await feedback_router.list_feedback(
        db, dimension=FeedbackDimension.root_cause_wrong
    )

    compiled = db.stmt.compile(dialect=postgresql.dialect())
    assert "run_feedback.dimensions @>" in str(compiled)
    assert ["root_cause_wrong"] in compiled.params.values()


async def test_stats_totals_and_breaks_down_by_agent():
    db = _ListDB(
        [
            ("frontend-triage", "up", 12),
            ("frontend-triage", "down", 32),
            ("bug-fix", "up", 6),
        ]
    )
    out = await feedback_router.feedback_stats(db)

    assert (out.up, out.down) == (18, 32)
    # Only rated agents are listed — the filter is built from this — and a
    # rating with no rows reads 0 rather than raising.
    assert [(a.agent, a.up, a.down) for a in out.by_agent] == [
        ("bug-fix", 6, 0),
        ("frontend-triage", 12, 32),
    ]


class _ConflictingDB(_FakeDB):
    """Raises the anon unique violation once, as a re-rating would."""

    def __init__(self, orig, **kwargs):
        super().__init__(**kwargs)
        self._orig = orig
        self._raised = False

    async def commit(self):
        if not self._raised:
            self._raised = True
            raise IntegrityError("INSERT", {}, self._orig)
        self.commits += 1


async def test_re_rating_updates_instead_of_duplicating():
    run = _FakeRun()
    db = _ConflictingDB(
        Exception(
            'duplicate key value violates unique constraint "uq_run_feedback_anon"'
        ),
        run=run,
        action=_FakeAction(),
    )

    out = await feedback_router.submit_feedback(
        feedback_links.mint_token(run.run_id),
        _payload(run.run_id, rating=FeedbackRating.down),
        db,
        x_rater_key="cookie-a",
    )

    assert db.rollbacks == 1
    assert db.updates == 1
    assert db.commits == 1
    # Insert and replace answer identically: whether we recognised this rater
    # is our bookkeeping, not something to tell them about.
    assert out.message == "Feedback recorded. Thank you."


async def test_unrelated_integrity_errors_are_not_swallowed():
    run = _FakeRun()
    db = _ConflictingDB(
        Exception('null value in column "rating" violates not-null constraint'),
        run=run,
        action=_FakeAction(),
    )

    with pytest.raises(IntegrityError):
        await feedback_router.submit_feedback(
            feedback_links.mint_token(run.run_id), _payload(run.run_id), db
        )

    assert db.updates == 0
