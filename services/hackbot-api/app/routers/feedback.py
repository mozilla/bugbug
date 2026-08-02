"""Read and record ratings of the Bugzilla comment a run posted.

Two groups of routes, split by who they serve. ``/rate/{token}`` backs the
public page anyone can reach from a Bugzilla comment; ``/feedback`` backs the
internal review page and is only ever called on behalf of a signed-in Mozillian.
Every route sits behind ``require_api_key`` regardless — the anonymous surface
is the Next.js page in hackbot-ui, which calls these server-side with the shared
key, so nothing here is directly reachable by the public.

The path split matters: hackbot-ui exempts ``/rate`` from its SSO middleware, so
keeping the public routes in their own namespace means anything added under
``/feedback`` later stays guarded by default.

The write path follows the upsert used by reviewhelper-api's ``/feedback``:
insert, catch the named unique violation, roll back and update. Re-rating
therefore replaces a rater's previous verdict instead of stacking duplicates.
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
from app.auth import require_api_key
from app.config import settings
from app.database.connection import get_db
from app.database.models import Run, RunAction, RunFeedback
from app.schemas import (
    AgentFeedbackStats,
    FeedbackCreate,
    FeedbackDimension,
    FeedbackDoc,
    FeedbackRating,
    FeedbackResponse,
    FeedbackStats,
    FeedbackTargetDoc,
    RaterKind,
    RunStatus,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])

_ANON_CONSTRAINT = "uq_run_feedback_anon"

# Whether a submission inserted or replaced an earlier one is our bookkeeping,
# not the rater's concern, so both paths answer identically.
_THANKS = "Feedback recorded. Thank you."

# Start of the footer the runtime appends to every agent comment at record time
# (see hackbot_runtime.actions.bugzilla).
_BUGZILLA_FOOTER = "*This is an automated analysis result."


def _analysis_only(text: str) -> str:
    """Drop the Bugzilla-facing footer from what the rating page shows.

    It directs the reader to file a needinfo if the analysis is wrong, which is
    both redundant and faintly contradictory on a page that exists to collect
    exactly that correction. The rating link the applier adds is never stored on
    the action, so it can't appear here in the first place.
    """
    head, found, _ = text.partition(_BUGZILLA_FOOTER)
    return head.rstrip() if found else text.rstrip()


async def _resolve_target(db: AsyncSession, token: str) -> tuple[UUID, RunAction]:
    """Map a token to the run and the comment action it may be rated against.

    Every failure raises the same 404: an unsigned token, an unknown run, a run
    that never succeeded and a run whose comment was never applied are
    indistinguishable from outside. Rating something Hackbot didn't actually
    post is the case reviewhelper-api's endpoint guards with a 422; here it
    collapses into the not-found response so nothing leaks about which runs
    exist.
    """
    run_id = feedback_links.verify_token(token)
    if run_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    run = await db.get(Run, run_id)
    if run is None or run.status != RunStatus.succeeded.value:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    result = await db.execute(
        select(RunAction)
        .where(
            RunAction.run_id == run_id,
            RunAction.type == "bugzilla.add_comment",
            RunAction.status == "applied",
        )
        .order_by(RunAction.idx)
    )
    action = result.scalars().first()
    if action is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return run_id, action


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

    Strictly read-only. Recording a vote here would hand one to every link
    prefetcher that touches the bugmail.
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

    # Excluding this rater's own row keeps someone who is merely changing their
    # mind from being turned away once a popular bug reaches the ceiling.
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

    dimensions = [dimension.value for dimension in request.dimensions]
    db.add(
        RunFeedback(
            run_id=run_id,
            rating=request.rating.value,
            dimensions=dimensions,
            comment=request.comment,
            rater_kind=RaterKind.anonymous.value,
            anon_id=anon_id,
        )
    )

    try:
        await db.commit()
    except IntegrityError as exc:
        if _ANON_CONSTRAINT not in str(exc.orig):
            raise
        # This rater already judged this run: replace their verdict rather than
        # stack a second row.
        await db.rollback()
        await db.execute(
            update(RunFeedback)
            .values(
                rating=request.rating.value,
                dimensions=dimensions,
                comment=request.comment,
            )
            .where(RunFeedback.run_id == run_id, RunFeedback.anon_id == anon_id)
        )
        await db.commit()

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

    Takes no `agent` or `rating` parameter on purpose. The caller needs the
    whole breakdown at once: to populate the agent filter with the agents that
    have actually been rated, and to show each thumb's count. Narrowing by
    either would collapse the very list being offered — the mistake that made
    an earlier client-side version of this filter disappear once used.
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
            created_at=row.created_at,
        )
        for row, row_agent, inputs in result.all()
    ]
