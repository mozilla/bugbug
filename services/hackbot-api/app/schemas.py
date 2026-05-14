from pydantic import BaseModel, Field

# --- Bug Fix ---


class BugFixRequest(BaseModel):
    bug_id: int
    model: str | None = None
    max_turns: int | None = None
    effort: str | None = None


class BugFixResponse(BaseModel):
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
