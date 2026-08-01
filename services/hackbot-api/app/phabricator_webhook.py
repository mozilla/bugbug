"""Phabricator webhook payload handling: Conduit reads + mention detection.

The webhook payload only carries PHIDs, so we call Conduit (via the shared
``phabricator_client`` lib) to fetch the triggering transactions,
detect an ``@hackbot`` mention, and resolve the revision to a revision id +
Bugzilla bug id. The route in ``app/routers/webhooks.py`` orchestrates these.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cachetools import TTLCache

if TYPE_CHECKING:
    from phabricator_client import PhabricatorClient

    from app.config import WebhookSettings

log = logging.getLogger(__name__)

# Transaction types that carry a comment we can scan for the mention.
_COMMENT_TYPES = frozenset({"comment", "inline"})
EDITBUGS_GROUP_PHID = "PHID-PROJ-njo5uuqyyq3oijbkhy55"
_MEMBERSHIP_CACHE_TTL_SECONDS = 60
_MISSING_MEMBER_REFRESH_COOLDOWN_SECONDS = 30

_editbugs_members_cache: TTLCache[str, frozenset[str]] = TTLCache(
    maxsize=1,
    ttl=_MEMBERSHIP_CACHE_TTL_SECONDS,
)
_editbugs_members_lock = asyncio.Lock()
_last_editbugs_members_refresh = 0.0


@dataclass(frozen=True)
class HackbotMention:
    raw: str
    author_phid: str


def triggering_transaction_phids(payload: dict) -> list[str]:
    """The transaction PHIDs this delivery is about (from the webhook body)."""
    return [
        t["phid"]
        for t in (payload.get("transactions") or [])
        if isinstance(t, dict) and t.get("phid")
    ]


def find_hackbot_mentions(
    transactions: list[dict],
    triggering_phids: set[str],
    *,
    bot_phid: str,
    token: str,
) -> list[HackbotMention]:
    """Return every triggering comment that mentions ``token``.

    Only considers transactions named in this delivery, of a comment type, not
    authored by the bot itself (loop prevention). A single review can leave
    several inline comments (each its own transaction), so all matches are
    returned, in transaction order. At most one per transaction: a transaction's
    ``comments`` list is that comment's version history, not distinct comments.
    """
    matches: list[HackbotMention] = []
    for transaction in transactions:
        if transaction.get("phid") not in triggering_phids:
            continue
        if transaction.get("type") not in _COMMENT_TYPES:
            continue
        author_phid = transaction.get("authorPHID")
        if not author_phid:
            continue
        if bot_phid and author_phid == bot_phid:
            continue
        for comment in transaction.get("comments") or []:
            raw = (comment.get("content") or {}).get("raw") or ""
            if token in raw:
                matches.append(
                    HackbotMention(
                        raw=raw,
                        author_phid=author_phid,
                    )
                )
                break
    return matches


def _join_comments(comments: list[str]) -> str:
    """Combine one or more triggering comments into the agent's ``comment`` input.

    A lone comment is passed through unchanged; multiple are numbered so the
    agent can tell them apart and address each.
    """
    if len(comments) == 1:
        return comments[0]
    return "\n\n".join(f"[comment {i}]\n{c}" for i, c in enumerate(comments, 1))


async def get_authorized_members(
    client: PhabricatorClient, author_phids: set[str]
) -> frozenset[str]:
    """Return the members authorized to trigger Hackbot for these authors.

    Known authors use the cached project-members snapshot. If any author is
    absent, refresh the snapshot once so newly added members take effect without
    waiting for the TTL. The cooldown prevents repeated unauthorized mentions
    from issuing a Phabricator request for every delivery.
    """
    global _last_editbugs_members_refresh

    if not author_phids:
        return frozenset()

    cached_members = _editbugs_members_cache.get(EDITBUGS_GROUP_PHID)
    if cached_members is not None and author_phids.issubset(cached_members):
        return cached_members

    # A shared async lock, so concurrent webhook requests do not issue duplicate refreshes.
    async with _editbugs_members_lock:
        cached_members = _editbugs_members_cache.get(EDITBUGS_GROUP_PHID)
        if cached_members is not None and author_phids.issubset(cached_members):
            return cached_members

        now = time.monotonic()
        if (
            cached_members is not None
            and now - _last_editbugs_members_refresh
            < _MISSING_MEMBER_REFRESH_COOLDOWN_SECONDS
        ):
            return cached_members

        members = await client.get_project_members(EDITBUGS_GROUP_PHID)
        _editbugs_members_cache[EDITBUGS_GROUP_PHID] = members
        _last_editbugs_members_refresh = time.monotonic()
        return members


async def resolve_revision(
    client: PhabricatorClient, revision_phid: str
) -> tuple[int | None, int | None]:
    """Resolve a DREV PHID to its ``(revision_id, bug_id)``.

    Either element is ``None`` if the revision can't be found or has no
    associated Bugzilla bug.
    """
    revision = await client.search_revision(revision_phid)
    if revision is None:
        return None, None
    revision_id = revision.get("id")
    fields = revision.get("fields") or {}
    bug_id_raw = fields.get("bugzilla.bug-id")
    try:
        bug_id = int(bug_id_raw) if bug_id_raw not in (None, "") else None
    except (TypeError, ValueError):
        bug_id = None
    return revision_id, bug_id


async def detect_mention_and_revision(
    client: PhabricatorClient,
    webhook: WebhookSettings,
    object_phid: str,
    triggering_phids: list[str],
) -> tuple[str, int, int] | None:
    """Read Conduit and return ``(comment, revision_id, bug_id)`` or None.

    ``comment`` is the raw text of the triggering ``@hackbot`` comment(s), passed
    through as data — the agent frames it (identity, scope, how to respond). When
    a delivery carries several qualifying comments (e.g. inline comments in one
    review) they are combined so the agent addresses each. The Conduit ``client``
    is injected (built by the route's dependency) rather than constructed here.
    Returns ``None`` when there is no qualifying ``@hackbot`` mention, the
    revision can't be resolved, or it has no Bugzilla bug id (bug-fix needs one).
    """
    transactions = await client.search_transactions(object_phid)
    mentions = find_hackbot_mentions(
        transactions,
        set(triggering_phids),
        bot_phid=webhook.bot_phid,
        token=webhook.mention_token,
    )
    authorized_members = await get_authorized_members(
        client,
        {mention.author_phid for mention in mentions},
    )
    comments: list[str] = []
    for mention in mentions:
        if mention.author_phid in authorized_members:
            comments.append(mention.raw)
        else:
            log.warning(
                "Ignoring %s mention from non-editbugs user %s on %s",
                webhook.mention_token,
                mention.author_phid,
                object_phid,
            )
    if not comments:
        log.warning(
            "No actionable %s mention found in triggering transactions %s on %s",
            webhook.mention_token,
            triggering_phids,
            object_phid,
        )
        return None
    comment = _join_comments(comments)

    revision_id, bug_id = await resolve_revision(client, object_phid)
    if revision_id is None:
        log.warning("Could not resolve revision for %s", object_phid)
        return None
    if bug_id is None:
        log.warning(
            "Revision D%s (%s) has no Bugzilla bug id; skipping",
            revision_id,
            object_phid,
        )
        return None

    return comment, revision_id, bug_id
