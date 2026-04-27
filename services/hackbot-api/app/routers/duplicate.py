from pathlib import Path

from fastapi import APIRouter

from app.config import settings
from app.schemas import DuplicateRequest, DuplicateResponse, DuplicateResultItem
from bugbug.tools.duplicate_bugs import DuplicateBugsTool

router = APIRouter(tags=["duplicate"])


@router.post("/duplicate", response_model=DuplicateResponse)
async def detect_duplicates(request: DuplicateRequest):
    tool = DuplicateBugsTool.create()
    result = await tool.run(
        mode=request.mode,
        base_url=settings.bz_base_url,
        api_key=settings.bz_api_key,
        meta_bug=request.meta_bug,
        bug_ids=request.bug_ids,
        local_dir=Path(request.local_dir) if request.local_dir else None,
        results_dir=Path(request.results_dir) if request.results_dir else None,
        model=request.model or settings.model,
        max_turns=request.max_turns or settings.max_turns,
    )
    return DuplicateResponse(
        exit_code=result.exit_code,
        results=[
            DuplicateResultItem(name=name, verdict=verdict)
            for name, verdict in result.results
        ],
    )
