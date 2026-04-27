from pathlib import Path

from fastapi import APIRouter

from app.config import settings
from app.schemas import TriageRequest, TriageResponse
from bugbug.tools.bug_fix import BugFixTool

router = APIRouter(tags=["triage"])


@router.post("/triage", response_model=TriageResponse)
async def triage_bugs(request: TriageRequest):
    tool = BugFixTool.create()
    result = await tool.run(
        base_url=settings.bz_base_url,
        api_key=settings.bz_api_key,
        source_repo=Path(settings.source_repo),
        bugs=request.bugs,
        keywords=request.keywords,
        blocks=request.blocks,
        status=request.status,
        instructions=request.instructions,
        task=request.task,
        rules_dir=Path(request.rules_dir) if request.rules_dir else None,
        dry_run=request.dry_run,
        newest_first=request.newest_first,
        model=request.model or settings.model,
        max_turns=request.max_turns or settings.max_turns,
        effort=request.effort or settings.effort,
    )
    return TriageResponse(
        exit_code=result.exit_code,
        bugs_processed=result.bugs_processed,
        simulated_writes=result.simulated_writes,
    )
