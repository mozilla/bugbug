"""Inbound webhook receivers that trigger hackbot runs.

Starts with Phabricator: an ``@hackbot`` mention in a comment on a Differential
revision triggers a bug-fix follow-up run against that revision. Authenticated
by Phabricator's HMAC signature (not the ``X-API-Key`` the other routes use), so
this lives on its own router without ``require_api_key``.
"""

import logging

from cachetools import TTLCache
from fastapi import APIRouter, Depends, Request, status
from phabricator_client import PhabricatorClient

from app.auth import require_phabricator_signature
from app.client import HackbotClient
from app.config import settings
from app.phabricator_webhook import (
    detect_mention_and_revision,
    triggering_transaction_phids,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks")


def get_phabricator_client() -> PhabricatorClient:
    """Dependency: a Conduit client built from the service's Phabricator config."""
    return PhabricatorClient(settings.phabricator)


def get_hackbot_client() -> HackbotClient:
    """Dependency: a client for triggering runs over the public hackbot API."""
    return HackbotClient(settings.hackbot_api_url, settings.external_api_key)


# Best-effort dedupe of retried deliveries, keyed by triggering transaction PHID.
# Per-instance and reset on restart; a durable dedupe (using the DB) can replace
# this if needed. Sized well above the number of mentions expected in a window.
_seen_transactions: TTLCache = TTLCache(
    maxsize=4096, ttl=settings.webhook.dedupe_ttl_seconds
)


@router.post(
    "/phabricator",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_phabricator_signature)],
)
async def phabricator_webhook(
    request: Request,
    phab_client: PhabricatorClient = Depends(get_phabricator_client),
    api_client: HackbotClient = Depends(get_hackbot_client),
) -> dict:
    payload = await request.json()

    action = payload.get("action") or {}
    if action.get("test"):
        # Phabricator's "test" ping when a webhook is created/edited.
        return {"status": "ignored", "reason": "test ping"}

    obj = payload.get("object") or {}
    if obj.get("type") != "DREV":
        return {"status": "ignored", "reason": "not a revision"}

    object_phid = obj.get("phid")
    triggering = triggering_transaction_phids(payload)
    if not object_phid or not triggering:
        return {"status": "ignored", "reason": "no revision or transactions"}

    # Dedupe retried deliveries: if we've already seen every triggering
    # transaction, this is a retry of work already handled.
    fresh = [phid for phid in triggering if phid not in _seen_transactions]
    if not fresh:
        return {"status": "ignored", "reason": "duplicate delivery"}

    # Only consider this delivery's fresh transactions for the mention, so a
    # payload mixing new and already-seen PHIDs can't re-trigger on an older one.
    detected = await detect_mention_and_revision(
        phab_client,
        settings.webhook,
        object_phid,
        fresh,
    )
    if detected is None:
        return {"status": "ignored", "reason": "no actionable @hackbot mention"}

    comment, revision_id, bug_id = detected

    run_id = await api_client.trigger_run(
        "bug-fix",
        {
            "bug_id": bug_id,
            "revision_id": revision_id,
            "comment": comment,
        },
    )
    # Mark seen only after a successful trigger: if detection or the trigger call
    # raises (transient Conduit/API failure), the delivery 500s and Phabricator's
    # retry must be reprocessed rather than dropped as a duplicate.
    for phid in fresh:
        _seen_transactions[phid] = True
    log.info(
        "Triggered bug-fix run %s for D%s (bug %s) from @hackbot mention",
        run_id,
        revision_id,
        bug_id,
    )
    return {"status": "triggered", "run_id": run_id}
