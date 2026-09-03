import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_push_auth
from app.database.connection import get_db
from app.database.models import Run
from app.routers.runs import finalize_run

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal/maintenance",
    dependencies=[Depends(require_push_auth)],
)


class StaleRunSweep(BaseModel):
    """What one sweep did, split by outcome so a caller can spot a stuck set."""

    considered: list[uuid.UUID]
    finalized: list[uuid.UUID]
    still_running: list[uuid.UUID]
    errored: list[uuid.UUID]


@router.post("/finalize-stale-runs", response_model=StaleRunSweep)
async def finalize_stale_runs(
    min_age_minutes: int = Query(default=120, ge=1),
    limit: int = Query(default=100, ge=1, le=1000),
    dry_run: bool = False,
    db: AsyncSession = Depends(get_db),
) -> StaleRunSweep:
    """Finalize runs whose completion event never arrived or never landed."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=min_age_minutes)
    result = await db.execute(
        select(Run)
        .where(Run.finalized_at.is_(None), Run.created_at < cutoff)
        .order_by(Run.created_at)
        .limit(limit)
    )
    runs = list(result.scalars())
    considered = [run.run_id for run in runs]

    if dry_run:
        log.info("Stale-run sweep (dry run) matched %d runs", len(runs))
        return StaleRunSweep(
            considered=considered, finalized=[], still_running=considered, errored=[]
        )

    finalized: list[uuid.UUID] = []
    still_running: list[uuid.UUID] = []
    errored: list[uuid.UUID] = []
    for run in runs:
        try:
            await finalize_run(db, run)
        except Exception:
            # One unfinalizable run must not abort the sweep for the rest.
            log.exception("Stale-run sweep could not finalize run %s", run.run_id)
            await db.rollback()
            errored.append(run.run_id)
            continue
        if run.finalized_at is not None:
            finalized.append(run.run_id)
        else:
            still_running.append(run.run_id)

    log.info(
        "Stale-run sweep finalized %d of %d runs (%d still running, %d errored)",
        len(finalized),
        len(considered),
        len(still_running),
        len(errored),
    )
    return StaleRunSweep(
        considered=considered,
        finalized=finalized,
        still_running=still_running,
        errored=errored,
    )
