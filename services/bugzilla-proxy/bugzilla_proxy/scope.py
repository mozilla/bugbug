"""What a run's token permits it to read. Pure logic, no I/O.

A bug is denied unless a grant positively admits it, and a bug whose ``groups``
we cannot see is denied outright rather than assumed public.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Least to most permissive; a bug matching several grants is served at the
# highest tier that admits it.
TIER_METADATA = "metadata"
TIER_FULL = "full"
TIERS = (TIER_METADATA, TIER_FULL)
_TIER_RANK = {tier: rank for rank, tier in enumerate(TIERS)}

# The metadata tier's default: enough to generate de-duplication candidates,
# not enough to read the bug.
DEFAULT_METADATA_FIELDS = frozenset(
    {
        "id",
        "summary",
        "product",
        "component",
        "status",
        "resolution",
        "dupe_of",
        "keywords",
        "creation_time",
        "last_change_time",
        "type",
        "severity",
        "priority",
        "is_open",
    }
)

# Needed to decide access. Added to every upstream request whatever the caller
# asked for, and stripped again on the way out unless the tier exposes them.
AUTH_FIELDS = frozenset(
    {
        "id",
        "groups",
        "product",
        "component",
        "status",
        "resolution",
        "keywords",
        "whiteboard",
        "blocks",
        "creation_time",
    }
)

# Rules BMO's own workflow controls. Any grant reaching private bugs needs one:
# the others are editable by anyone with `editbugs`, who could then enlarge a
# run's footprint.
STRUCTURAL_RULES = frozenset(
    {"static_bugs", "product", "component", "status", "resolution", "created_after"}
)


def _as_int_set(values: Iterable[Any] | None) -> frozenset[int]:
    return frozenset(int(v) for v in values or ())


def _as_lower_set(values: Iterable[Any] | None) -> frozenset[str]:
    return frozenset(str(v).lower() for v in values or ())


def _parse_time(raw: str) -> datetime:
    """Parse a BMO timestamp, which is ISO 8601 with a ``Z`` suffix."""
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@dataclass(frozen=True)
class Anchor:
    """The filters deciding which bugs fall inside one grant.

    Every configured rule must match (AND, not OR), and an unconfigured rule is
    ignored, so an empty anchor matches everything and is only valid on a grant
    that cannot reach private data.
    """

    static_bugs: frozenset[int] = frozenset()
    product: frozenset[str] = frozenset()
    component: frozenset[str] = frozenset()
    status: frozenset[str] = frozenset()
    resolution: frozenset[str] = frozenset()
    created_after: str | None = None
    keywords: frozenset[str] = frozenset()
    whiteboard: tuple[str, ...] = ()
    blocks: frozenset[int] = frozenset()

    @classmethod
    def from_claim(cls, raw: Mapping[str, Any]) -> Anchor:
        return cls(
            static_bugs=_as_int_set(raw.get("static_bugs")),
            product=frozenset(str(p) for p in raw.get("product") or ()),
            component=frozenset(str(c) for c in raw.get("component") or ()),
            status=frozenset(str(s).upper() for s in raw.get("status") or ()),
            resolution=frozenset(str(r).upper() for r in raw.get("resolution") or ()),
            created_after=raw.get("created_after"),
            keywords=_as_lower_set(raw.get("keywords")),
            whiteboard=tuple(str(w) for w in raw.get("whiteboard") or ()),
            blocks=_as_int_set(raw.get("blocks")),
        )

    def configured_rules(self) -> frozenset[str]:
        """Names of the rules this anchor actually constrains."""
        present = {
            "static_bugs": self.static_bugs,
            "product": self.product,
            "component": self.component,
            "status": self.status,
            "resolution": self.resolution,
            "created_after": self.created_after,
            "keywords": self.keywords,
            "whiteboard": self.whiteboard,
            "blocks": self.blocks,
        }
        return frozenset(name for name, value in present.items() if value)

    def has_structural_rule(self) -> bool:
        return bool(self.configured_rules() & STRUCTURAL_RULES)

    def matches(self, bug: Mapping[str, Any]) -> bool:
        """True if every configured rule holds for ``bug``."""
        if self.static_bugs and int(bug.get("id", -1)) not in self.static_bugs:
            return False
        if self.product and bug.get("product") not in self.product:
            return False
        if self.component and bug.get("component") not in self.component:
            return False
        if self.status and str(bug.get("status", "")).upper() not in self.status:
            return False
        if (
            self.resolution
            and str(bug.get("resolution", "")).upper() not in self.resolution
        ):
            return False
        if self.created_after:
            created = bug.get("creation_time")
            if not created:
                return False
            try:
                if _parse_time(str(created)) < _parse_time(self.created_after):
                    return False
            except ValueError:
                return False
        if self.keywords and not (_as_lower_set(bug.get("keywords")) & self.keywords):
            return False
        if self.whiteboard:
            board = str(bug.get("whiteboard") or "")
            if not any(tag in board for tag in self.whiteboard):
                return False
        if self.blocks and not (_as_int_set(bug.get("blocks")) & self.blocks):
            return False
        return True


@dataclass(frozen=True)
class Grant:
    """One tier of access over one set of bugs."""

    tier: str
    anchor: Anchor
    # A ceiling, not a filter: a bug in any group outside this is denied, while
    # a public bug matching the anchor is served regardless. Empty means public
    # only, which is what makes private access opt-in.
    groups: frozenset[str] = frozenset()
    # Empty falls back to the tier default.
    fields: frozenset[str] = frozenset()
    endpoints: tuple[str, ...] = ()

    @classmethod
    def from_claim(cls, raw: Mapping[str, Any]) -> Grant:
        anchor_claim = dict(raw.get("anchor") or {})
        # Rides inside `anchor` on the wire, but it is a ceiling not a filter,
        # so it lives on the grant here.
        groups = frozenset(str(g) for g in anchor_claim.pop("groups", None) or ())
        return cls(
            tier=str(raw.get("tier", "")),
            anchor=Anchor.from_claim(anchor_claim),
            groups=groups,
            fields=frozenset(str(f) for f in raw.get("fields") or ()),
            endpoints=tuple(str(e) for e in raw.get("endpoints") or ()),
        )

    @property
    def is_private(self) -> bool:
        return bool(self.groups)

    def permits(self, bug: Mapping[str, Any]) -> bool:
        """True if this grant admits ``bug``.

        A missing ``groups`` field is a denial, not an assumption of public:
        we always request it, so its absence means something went wrong, and
        guessing is how a security bug leaks.
        """
        if "groups" not in bug:
            return False
        if not frozenset(bug.get("groups") or ()) <= self.groups:
            return False
        return self.anchor.matches(bug)

    def allows_endpoint(self, path: str) -> bool:
        """True if ``path`` matches a pattern, ``*`` being exactly one segment."""
        segments = [s for s in path.strip("/").split("/") if s]
        for pattern in self.endpoints:
            expected = [s for s in pattern.strip("/").split("/") if s]
            if len(expected) != len(segments):
                continue
            if all(e == "*" or e == got for e, got in zip(expected, segments)):
                return True
        return False

    def visible_fields(self) -> frozenset[str] | None:
        """The fields this grant exposes, or None for whatever upstream sent."""
        if self.fields:
            return self.fields
        if self.tier == TIER_METADATA:
            return DEFAULT_METADATA_FIELDS
        return None

    def project(
        self, bug: Mapping[str, Any], requested: frozenset[str] | None = None
    ) -> dict[str, Any]:
        """Drop what the tier does not expose, then narrow to ``requested``.

        ``requested`` is the caller's ``include_fields``; it can only narrow.
        This is also where the added :data:`AUTH_FIELDS` come back off.
        """
        visible = self.visible_fields()
        keys = set(bug) if visible is None else (set(bug) & visible)
        if requested:
            keys &= requested
        return {key: bug[key] for key in keys}


@dataclass(frozen=True)
class Scope:
    """A verified token, in the form the request path needs."""

    run_id: str
    agent: str
    jti: str
    requested_by: str | None = None
    read_only: bool = True
    confidential: bool = False
    attachments: bool = False
    filter_content: str = "off"
    promotions_max: int = 0
    grants: tuple[Grant, ...] = field(default_factory=tuple)

    @property
    def is_private(self) -> bool:
        return any(grant.is_private for grant in self.grants)

    def grant_for(self, bug: Mapping[str, Any]) -> Grant | None:
        """The highest-tier grant admitting ``bug``, or None."""
        best: Grant | None = None
        for grant in self.grants:
            if not grant.permits(bug):
                continue
            if best is None or _TIER_RANK[grant.tier] > _TIER_RANK[best.tier]:
                best = grant
        return best

    def grant_for_endpoint(self, bug: Mapping[str, Any], path: str) -> Grant | None:
        """The highest-tier grant admitting ``bug`` and exposing ``path``.

        Separate from :meth:`grant_for` because tiers differ in endpoints: a
        bug can be readable at the metadata tier while its comments are not.
        """
        best: Grant | None = None
        for grant in self.grants:
            if not grant.permits(bug) or not grant.allows_endpoint(path):
                continue
            if best is None or _TIER_RANK[grant.tier] > _TIER_RANK[best.tier]:
                best = grant
        return best

    def upstream_fields(self, requested: frozenset[str] | None) -> frozenset[str]:
        """The fields to ask upstream for, given what the caller asked for.

        Always a superset of :data:`AUTH_FIELDS`, so the decision never depends
        on the caller's ``include_fields``.
        """
        if requested:
            return frozenset(requested) | AUTH_FIELDS
        widest: frozenset[str] | None = frozenset()
        for grant in self.grants:
            visible = grant.visible_fields()
            if visible is None:
                widest = None
                break
            widest = (widest or frozenset()) | visible
        if widest is None:
            # A grant serves whole bugs. Name `_default` explicitly rather
            # than omitting `include_fields`, so the auth fields stay present
            # even if BMO's default set changes.
            return frozenset({"_default"}) | AUTH_FIELDS
        return widest | AUTH_FIELDS
