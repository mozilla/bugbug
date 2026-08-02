"""Read and record ratings of the Bugzilla comment a run posted.

``/rate/{token}`` backs the public page anyone can reach from a Bugzilla
comment; ``/feedback`` backs the internal review page. Every route sits behind
``require_api_key`` regardless — the anonymous surface is the Next.js page in
hackbot-ui, which calls these server-side with the shared key.

The write path follows the upsert in reviewhelper-api's ``/feedback``: insert,
catch the named unique violation, roll back and update, so re-rating replaces
rather than duplicates.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import feedback_links
from app.actions_applier import rating_enabled
from app.auth import require_api_key
from app.config import settings
from app.database.connection import get_db
from app.database.models import Run, RunAction, RunFeedback
from app.routers.runs import UserEmail
from app.schemas import (
    AgentFeedbackStats,
    FeedbackCreate,
    FeedbackDimension,
    FeedbackDoc,
    FeedbackRating,
    FeedbackResponse,
    FeedbackStats,
    FeedbackTargetDoc,
    InternalFeedbackCreate,
    RaterKind,
    RunStatus,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])

_ANON_CONSTRAINT = "uq_run_feedback_anon"
_RATER_CONSTRAINT = "uq_run_feedback_rater"

# Whether a submission inserted or replaced an earlier one is our bookkeeping,
# not the rater's concern, so both paths answer identically.
_THANKS = "Feedback recorded. Thank you."

# Start of the footer the runtime appends to every agent comment at record time
# (see hackbot_runtime.actions.bugzilla).
_BUGZILLA_FOOTER = "*This is an automated analysis result."


def _analysis_only(text: str) -> str:
    """Drop the Bugzilla-facing footer from what the rating page shows.

    It directs the reader to file a needinfo, which contradicts the page they
    are already on.
    """
    head, found, _ = text.partition(_BUGZILLA_FOOTER)
    return head.rstrip() if found else text.rstrip()


async def _comment_action(
    db: AsyncSession, run_id: UUID, *, applied_only: bool
) -> RunAction:
    """The comment on `run_id` that may be rated, or 404.

    `applied_only` separates the two entry points: a public rater can only judge
    what Bugzilla received, a signed-in reviewer judges the proposal — including
    one they then decline to post.
    """
    run = await db.get(Run, run_id)
    if run is None or run.status != RunStatus.succeeded.value:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    stmt = select(RunAction).where(
        RunAction.run_id == run_id,
        RunAction.type == "bugzilla.add_comment",
    )
    if applied_only:
        stmt = stmt.where(RunAction.status == "applied")

    action = (await db.execute(stmt.order_by(RunAction.idx))).scalars().first()
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return action


async def _resolve_target(db: AsyncSession, token: str) -> tuple[UUID, RunAction]:
    """Map a public token to the run and the posted comment it may rate.

    Every failure raises the same 404 — unsigned token, unknown run, run that
    never succeeded, run whose comment was never applied — so nothing leaks
    about which runs exist.
    """
    run_id = feedback_links.verify_token(token)
    if run_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return run_id, await _comment_action(db, run_id, applied_only=True)


async def _record(
    db: AsyncSession,
    run_id: UUID,
    request: FeedbackCreate | InternalFeedbackCreate,
    *,
    rater_kind: RaterKind,
    constraint: str,
    rater_id: str | None = None,
    anon_id: str | None = None,
) -> None:
    """Insert this rater's verdict, or replace the one they left before.

    Insert-then-catch rather than a pre-check, so two submissions racing can't
    both decide they are the first. Shared by both entry points; they differ
    only in which dedupe key, and so which unique index, identifies the rater.
    """
    dimensions = [dimension.value for dimension in request.dimensions]
    db.add(
        RunFeedback(
            run_id=run_id,
            rating=request.rating.value,
            dimensions=dimensions,
            comment=request.comment,
            rater_kind=rater_kind.value,
            rater_id=rater_id,
            anon_id=anon_id,
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        if constraint not in str(exc.orig):
            raise
        await db.rollback()
        key = (
            RunFeedback.rater_id == rater_id
            if rater_id is not None
            else RunFeedback.anon_id == anon_id
        )
        await db.execute(
            update(RunFeedback)
            .values(
                rating=request.rating.value,
                dimensions=dimensions,
                comment=request.comment,
            )
            .where(RunFeedback.run_id == run_id, key)
        )
        await db.commit()


@router.get(
    "/rate/{token}",
    response_model=FeedbackTargetDoc,
    dependencies=[Depends(require_api_key)],
)
async def get_feedback_target(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FeedbackTargetDoc:
    """Return the posted comment being rated, plus a nonce for the write.

    Strictly read-only: recording here would hand a vote to every prefetcher.
    """
    run_id, action = await _resolve_target(db, token)
    return FeedbackTargetDoc(
        bug_id=action.params["bug_id"],
        comment=_analysis_only(action.params["text"]),
        nonce=feedback_links.mint_nonce(run_id),
    )


@router.post(
    "/rate/{token}",
    response_model=FeedbackResponse,
    dependencies=[Depends(require_api_key)],
)
async def submit_feedback(
    token: str,
    request: FeedbackCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_rater_key: Annotated[str | None, Header()] = None,
    x_forwarded_for: Annotated[str | None, Header()] = None,
    user_agent: Annotated[str | None, Header()] = None,
) -> FeedbackResponse:
    run_id, _ = await _resolve_target(db, token)

    if not feedback_links.verify_nonce(run_id, request.nonce):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This feedback form has expired. Reload the page and try again.",
        )

    anon_id = feedback_links.anon_id(
        x_rater_key, feedback_links.client_ip_from(x_forwarded_for), user_agent
    )

    # Excluding this rater's own row lets someone change their mind even once
    # the ceiling is reached.
    others = await db.scalar(
        select(func.count())
        .select_from(RunFeedback)
        .where(
            RunFeedback.run_id == run_id,
            RunFeedback.rater_kind == RaterKind.anonymous.value,
            RunFeedback.anon_id.is_distinct_from(anon_id),
        )
    )
    if others >= settings.feedback_max_anonymous_votes:
        log.warning("Anonymous feedback cap reached for run %s", run_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="This analysis has already received the maximum number of ratings.",
        )

    await _record(
        db,
        run_id,
        request,
        rater_kind=RaterKind.anonymous,
        constraint=_ANON_CONSTRAINT,
        anon_id=anon_id,
    )
    return FeedbackResponse(message=_THANKS)


@router.post(
    "/runs/{run_id}/feedback",
    response_model=FeedbackResponse,
    dependencies=[Depends(require_api_key)],
)
async def submit_internal_feedback(
    run_id: UUID,
    request: InternalFeedbackCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    x_on_behalf_of: UserEmail = Header(default=None),
) -> FeedbackResponse:
    """Record a signed-in reviewer's rating of a run's proposed comment.

    Unlike the public route this accepts a comment that has only been recorded,
    so a reviewer can reject an analysis and decline to post it — the clearest
    signal the agent got something wrong. Identity comes from the caller's
    session via hackbot-ui, never from the request body. Gated on the same
    registry flag as the Bugzilla link, so an agent opts in once for both.
    """
    if not x_on_behalf_of:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-On-Behalf-Of is required",
        )

    run = await db.get(Run, run_id)
    if run is None or not rating_enabled(run.agent):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    await _comment_action(db, run_id, applied_only=False)
    await _record(
        db,
        run_id,
        request,
        rater_kind=RaterKind.mozilla,
        constraint=_RATER_CONSTRAINT,
        rater_id=x_on_behalf_of,
    )
    return FeedbackResponse(message=_THANKS)


@router.get(
    "/feedback/stats",
    response_model=FeedbackStats,
    dependencies=[Depends(require_api_key)],
)
async def feedback_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    run_id: UUID | None = None,
) -> FeedbackStats:
    """Rating totals, broken down by agent, independent of paging.

    Takes no `agent` or `rating` parameter on purpose: this also populates the
    agent filter's options, so narrowing it would collapse the list it exists
    to build.
    """
    stmt = (
        select(Run.agent, RunFeedback.rating, func.count())
        .select_from(RunFeedback)
        .join(Run, Run.run_id == RunFeedback.run_id)
        .group_by(Run.agent, RunFeedback.rating)
    )
    if run_id:
        stmt = stmt.where(RunFeedback.run_id == run_id)

    per_agent: dict[str, dict[str, int]] = {}
    for agent, rating, count in (await db.execute(stmt)).all():
        per_agent.setdefault(agent, {})[rating] = count

    by_agent = [
        AgentFeedbackStats(
            agent=agent,
            up=counts.get(FeedbackRating.up.value, 0),
            down=counts.get(FeedbackRating.down.value, 0),
        )
        for agent, counts in sorted(per_agent.items())
    ]
    return FeedbackStats(
        up=sum(a.up for a in by_agent),
        down=sum(a.down for a in by_agent),
        by_agent=by_agent,
    )


@router.get(
    "/feedback",
    response_model=list[FeedbackDoc],
    dependencies=[Depends(require_api_key)],
)
async def list_feedback(
    db: Annotated[AsyncSession, Depends(get_db)],
    agent: str | None = None,
    rating: FeedbackRating | None = None,
    dimension: FeedbackDimension | None = None,
    run_id: UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[FeedbackDoc]:
    """Every rating, newest first, for the internal review page.

    Joined to `runs` because `agent` and the bug id live there — feedback rows
    only carry a run id. Unlike the public routes this returns rater metadata
    and the run it belongs to, which is why the page fronting it is behind SSO.
    """
    stmt = (
        select(RunFeedback, Run.agent, Run.inputs)
        .join(Run, Run.run_id == RunFeedback.run_id)
        .order_by(RunFeedback.created_at.desc())
        .limit(min(limit, 500))
        .offset(offset)
    )
    if agent:
        stmt = stmt.where(Run.agent == agent)
    if rating:
        stmt = stmt.where(RunFeedback.rating == rating.value)
    if dimension:
        # JSONB containment (@>): the row's dimension array includes this label.
        stmt = stmt.where(RunFeedback.dimensions.contains([dimension.value]))
    if run_id:
        stmt = stmt.where(RunFeedback.run_id == run_id)

    result = await db.execute(stmt)
    return [
        FeedbackDoc(
            run_id=row.run_id,
            agent=row_agent,
            bug_id=(inputs or {}).get("bug_id"),
            rating=row.rating,
            dimensions=row.dimensions,
            comment=row.comment,
            rater_kind=row.rater_kind,
            rater_id=row.rater_id,
            created_at=row.created_at,
        )
        for row, row_agent, inputs in result.all()
    ]
