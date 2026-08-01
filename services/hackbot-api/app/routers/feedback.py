"""Read and record ratings of the Bugzilla comment a run posted.

Both routes sit behind ``require_api_key`` like the rest of the API. The
anonymous surface is the Next.js page in hackbot-ui, which calls these
server-side with the shared key — so nothing here is directly reachable by the
public, and the trust boundary stays where the rest of the service expects it.

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
    FeedbackCreate,
    FeedbackResponse,
    FeedbackTargetDoc,
    RaterKind,
    RunStatus,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/feedback", tags=["feedback"])

_ANON_CONSTRAINT = "uq_run_feedback_anon"


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
    "/{token}",
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
        comment=action.params["text"],
        nonce=feedback_links.mint_nonce(run_id),
    )


@router.post(
    "/{token}",
    response_model=FeedbackResponse,
    dependencies=[Depends(require_api_key)],
)
async def submit_feedback(
    token: str,
    request: FeedbackCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
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
        feedback_links.client_ip_from(x_forwarded_for), user_agent
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
        return FeedbackResponse(message="Feedback updated. Thank you.")

    return FeedbackResponse(message="Feedback recorded. Thank you.")
