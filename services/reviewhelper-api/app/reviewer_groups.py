"""Per-reviewer-group configuration for automated code review.

This is the shared config that risk/complexity gating (and, later, group-specific
skills and member-author restriction) reads. It is a checked-in YAML file rather
than env vars or a DB table: the config is a small, structured, version-controlled
list, and "easing in" a group is a reviewed change to the file.

The file path defaults to ``reviewer_groups.yaml`` at the service root and can be
overridden with the ``REVIEWER_GROUPS_CONFIG`` environment variable.
"""

import os
from functools import cache
from logging import getLogger
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

logger = getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "reviewer_groups.yaml"


class Defaults(BaseModel):
    """Fallback thresholds applied to groups that don't override them."""

    # A review runs only when BOTH scores are strictly below their threshold.
    risk_threshold: int = 3
    complexity_threshold: int = 3


class ReviewerGroup(BaseModel):
    """Configuration for a single Phabricator reviewer group (project)."""

    slug: str
    # Additional project slugs that count as this same logical group — e.g. a
    # review rotation's project (`<group>-rotation`), which is added as the
    # reviewer and then swapped for an individual member.
    aliases: list[str] = Field(default_factory=list)
    enabled: bool = False
    risk_threshold: int | None = None
    complexity_threshold: int | None = None
    # When True, only review revisions authored by a member of this group's
    # Phabricator project. Lets a team dogfood automated review on its own
    # patches before opening it to outside contributors.
    restrict_to_member_authors: bool = True
    # PHIDs of group members who have opted out of automated review on their
    # own patches.
    opt_out: list[str] = Field(default_factory=list)

    def all_slugs(self) -> list[str]:
        """The group's own slug plus any alias slugs."""
        return [self.slug, *self.aliases]

    def effective_risk_threshold(self, defaults: Defaults) -> int:
        return (
            self.risk_threshold
            if self.risk_threshold is not None
            else defaults.risk_threshold
        )

    def effective_complexity_threshold(self, defaults: Defaults) -> int:
        return (
            self.complexity_threshold
            if self.complexity_threshold is not None
            else defaults.complexity_threshold
        )


class ReviewerGroupsConfig(BaseModel):
    defaults: Defaults = Field(default_factory=Defaults)
    groups: list[ReviewerGroup] = Field(default_factory=list)

    def by_slug(self, slug: str) -> ReviewerGroup | None:
        for group in self.groups:
            if group.slug == slug:
                return group
        return None


def _config_path() -> Path:
    return Path(os.getenv("REVIEWER_GROUPS_CONFIG", str(_DEFAULT_CONFIG_PATH)))


@cache
def get_reviewer_groups_config() -> ReviewerGroupsConfig:
    """Load and cache the reviewer-groups config for the process lifetime."""
    path = _config_path()
    if not path.exists():
        logger.warning(
            "reviewer groups config not found at %s; using empty config", path
        )
        return ReviewerGroupsConfig()
    data = yaml.safe_load(path.read_text()) or {}
    return ReviewerGroupsConfig.model_validate(data)


def matching_groups(patch) -> list[ReviewerGroup]:
    """Return the configured groups the patch is requesting review from.

    A patch matches a group when any of the group's slugs (its own or an alias)
    resolves to a project that was a reviewer of the revision. We match against
    ``historical_reviewer_project_phids`` so a rotation group that was added and
    later removed (replaced by an individual) still counts; falls back to the
    current reviewer snapshot for patches that don't expose history. The result
    preserves config order so the first match can act as the "primary" group.
    """
    # Imported here so the heavy Phabricator/searchfox import chain isn't pulled
    # in just to parse the config (and so tests can monkeypatch the resolver).
    from bugbug.tools.core.platforms import phabricator as phab

    config = get_reviewer_groups_config()
    reviewer_projects = set(
        getattr(patch, "historical_reviewer_project_phids", None)
        or patch.reviewer_project_phids
    )
    if not reviewer_projects:
        return []

    matched = []
    for group in config.groups:
        group_phids = {phab.resolve_project_phid(slug) for slug in group.all_slugs()}
        group_phids.discard(None)
        if group_phids & reviewer_projects:
            matched.append(group)
    return matched
