from pydantic import BaseModel, Field

# --- Triage ---


class TriageRequest(BaseModel):
    bugs: list[int] | None = None
    keywords: list[str] | None = None
    blocks: int | None = None
    status: list[str] | None = None
    instructions: str = ""
    task: str | None = None
    rules_dir: str | None = None
    dry_run: bool = True  # Default to dry-run for safety
    newest_first: bool = False
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None


class TriageResponse(BaseModel):
    exit_code: int
    bugs_processed: int
    simulated_writes: list[dict] = Field(default_factory=list)


# --- Duplicate Detection ---


class DuplicateRequest(BaseModel):
    mode: str  # "local" | "bugs" | "local_to_local"
    meta_bug: int | None = None
    bug_ids: list[int] | None = None
    local_dir: str | None = None
    results_dir: str | None = None
    model: str | None = None
    max_turns: int | None = None


class DuplicateResultItem(BaseModel):
    name: str
    verdict: str


class DuplicateResponse(BaseModel):
    exit_code: int
    results: list[DuplicateResultItem]
