"""Models and source types for the uplift agent.

Each source kind knows how to fetch what it needs (`fetch_diff`) and how to
describe itself in the prompt (`render_work_item`), so a new kind touches only
this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Union

from hackbot_runtime import AgentError
from phabricator_client import (
    PhabricatorClient,
    PhabricatorDiff,
    UnresolvedCommitError,
)
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

MODEL = "claude-opus-5"

# Mirrors `FULL_SHA_PATTERN` in hackbot-api: git will not fetch an abbreviated
# object id from a remote, and every commit here is fetched from one.
FULL_SHA_PATTERN = r"^[0-9a-f]{40}$"


@dataclass(frozen=True)
class FetchedDiff:
    """A source's diff on disk, and what the agent needs to apply it."""

    path: Path

    # ``"Name <email>"``, or ``None`` when Phabricator recorded none.
    # Reported with the run for the reviewer; Lando re-attributes the patch
    # itself when it re-creates the revisions.
    author: str | None

    # The commit the diff was built on. Until it is fetched the blobs the diff
    # names are missing from the shallow checkout, and `--3way` silently
    # degrades to a direct apply that leaves no conflict markers.
    base_commit: str | None = None

    # The diff actually fetched, which an unpinned source does not name.
    diff_id: int | None = None


class GitSource(BaseModel):
    """A patch to uplift, identified by a git commit in the Firefox repo."""

    kind: Literal["git"] = "git"
    commit: str = Field(
        pattern=FULL_SHA_PATTERN, description="Full git commit SHA to cherry-pick."
    )

    async def fetch_diff(
        self, client: PhabricatorClient, scratch_out: Path
    ) -> FetchedDiff | None:
        """Nothing to fetch: the commit is already in the repo to cherry-pick."""
        return None

    def render_work_item(self, index: int, fetched: FetchedDiff | None) -> str:
        """Describe this source as a numbered work item for the prompt."""
        return f"{index}. git commit `{self.commit}` — cherry-pick it."


class PhabricatorSource(BaseModel):
    """A patch to uplift, identified by a Phabricator revision.

    The diff is fetched through the broker rather than passed in: run inputs
    reach the job as environment variables, which a large diff will not fit.
    """

    kind: Literal["phabricator"] = "phabricator"
    revision_id: int = Field(description="Phabricator revision id (the D-number).")

    # Pin the exact diff. Lando knows which one it landed; without a pin, a
    # revision updated since the request resolves to different code.
    diff_id: int | None = Field(
        default=None,
        description="Diff to uplift; defaults to the revision's latest.",
    )

    def diff_path(self, scratch_out: Path, diff_id: int) -> Path:
        """The on-disk path a given diff of this revision is written to.

        Named by diff too: sources are all fetched up front, so two pins on one
        revision would otherwise share a file and lose one of them.
        """
        return scratch_out / f"D{self.revision_id}-{diff_id}.diff"

    async def resolve_diff(self, client: PhabricatorClient) -> PhabricatorDiff:
        """The diff to uplift: the pinned one, else the revision's latest.

        Raises :class:`AgentError` for a revision with no diffs, or a pin that
        does not belong to it, rather than uplifting other code.
        """
        result = await client.conduit_request(
            "differential.querydiffs", revisionIDs=[self.revision_id]
        )
        by_id = {int(raw["id"]): raw for raw in (result or {}).values()}
        if not by_id:
            raise AgentError(f"D{self.revision_id} has no diffs to uplift")

        if self.diff_id is None:
            raw = by_id[max(by_id)]
        elif self.diff_id in by_id:
            raw = by_id[self.diff_id]
        else:
            raise AgentError(
                f"diff {self.diff_id} does not belong to D{self.revision_id}"
            )

        return PhabricatorDiff.model_validate(raw)

    async def fetch_diff(
        self, client: PhabricatorClient, scratch_out: Path
    ) -> FetchedDiff | None:
        """Write this revision's diff to disk and return what to apply it with."""
        diff = await self.resolve_diff(client)
        path = self.diff_path(scratch_out, diff.id)
        path.write_text(await client.get_raw_diff(diff.id))
        return FetchedDiff(
            path=path,
            author=diff.author,
            base_commit=await self.resolve_base(client, diff),
            diff_id=diff.id,
        )

    async def resolve_base(
        self, client: PhabricatorClient, diff: PhabricatorDiff
    ) -> str | None:
        """The diff's base commit as a full hash, the only kind git will fetch.

        moz-phab abbreviates it for a repo the size of firefox. One that cannot
        be expanded is reported as none: it is a hint, and the prompt covers
        going without.
        """
        if diff.base_commit is None:
            return None
        try:
            return await client.resolve_commit(diff.base_commit)
        except UnresolvedCommitError:
            logger.warning(
                "could not expand base commit %s of D%s to a full hash",
                diff.base_commit,
                self.revision_id,
            )
            return None

    def render_work_item(self, index: int, fetched: FetchedDiff | None) -> str:
        """Describe this source as a numbered work item for the prompt.

        ``fetched`` is this revision's own :meth:`fetch_diff` result and so is
        never ``None``; the type is optional only because a git source fetches
        nothing. Fail rather than name a path no diff was written to.
        """
        if fetched is None:
            raise AgentError(
                f"D{self.revision_id}: diff must be fetched before it is rendered"
            )
        return (
            f"{index}. Phabricator revision D{self.revision_id} — the diff is at "
            f"`{fetched.path}`, {self.render_base(fetched)}. Commit it on "
            f"its own."
        )

    def render_base(self, fetched: FetchedDiff) -> str:
        """The base commit to fetch before applying, or that there is none."""
        if fetched.base_commit is None:
            return "and Phabricator recorded no base commit for it"
        return f"built on base commit `{fetched.base_commit}`"


# Sources are applied in order, and a run may mix the two kinds.
UpliftSource = Annotated[
    Union[GitSource, PhabricatorSource], Field(discriminator="kind")
]


class RequestedSource(BaseModel):
    """What one requested source resolved to, before the agent touched it.

    A record of what was asked for and what the fetch pinned it down to, not
    evidence any of it was applied -- the report and the checks on the checkout
    speak to that.
    """

    # The source as supplied. A raw mapping, to stay agnostic about the kinds.
    source: dict

    # The diff actually used, which differs from the input when nothing was
    # pinned. Phabricator sources only.
    diff_id: int | None = None

    base_commit: str | None = None
    author: str | None = None


class ConflictReport(BaseModel):
    """One file the agent resolved, as it reports it.

    Tolerant on purpose: a malformed entry should not cost the whole report.
    """

    model_config = ConfigDict(extra="ignore")

    file: str = ""
    resolution: str = ""


class Report(BaseModel):
    """The agent's ``report.json``, in the shape the system prompt specifies.

    Every field defaults, so a partial report still parses. Validation is what
    catches a plausible but wrong value: `"false"` is a string `bool()` reads
    as `True`, and a caller gates on `confidence`.
    """

    model_config = ConfigDict(extra="ignore")

    resolved: bool = False
    confidence: Literal["high", "medium", "low"] | None = None
    summary: str = ""
    conflicts: list[ConflictReport] = []
    unresolved: list[str] = []
