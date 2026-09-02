"""Inbound webhooks that trigger Hackbot runs."""

import logging

from cachetools import TTLCache
from fastapi import APIRouter, Depends, Request, status
from hackbot_client import HackbotClient
from phabricator_client import PhabricatorClient

from app.auth import (
    require_bugzilla_webhook_secret,
    require_phabricator_signature,
)
from app.bugzilla_webhook import detect_needinfo_request
from app.config import settings
from app.phabricator_authorization import (
    AUTHORIZED_GROUP_PHID,
    PhabricatorAuthorizer,
)
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
    return HackbotClient(
        base_url=settings.hackbot_api_url,
        api_key=settings.external_api_key,
    )


def get_phabricator_authorizer(
    request: Request,
    phab_client: PhabricatorClient = Depends(get_phabricator_client),
) -> PhabricatorAuthorizer:
    """Dependency: lazily create the app-scoped authorizer and its member cache."""
    authorizer = getattr(request.app.state, "phabricator_authorizer", None)
    if authorizer is None:
        authorizer = PhabricatorAuthorizer(phab_client, AUTHORIZED_GROUP_PHID)
        request.app.state.phabricator_authorizer = authorizer
    return authorizer


# Best-effort dedupe of retried deliveries, keyed by triggering transaction PHID.
# Per-instance and reset on restart; a durable dedupe (using the DB) can replace
# this if needed. Sized well above the number of mentions expected in a window.
_seen_transactions: TTLCache = TTLCache(
    maxsize=4096, ttl=settings.webhook.dedupe_ttl_seconds
)

# Best-effort dedupe of retried BMO deliveries, keyed by the globally unique
# needinfo flag ID. A later needinfo on the same bug receives a new flag ID.
# TODO: Replace with DB-level deduplication (#6716).
_seen_bugzilla_events: TTLCache = TTLCache(
    maxsize=4096, ttl=settings.bugzilla_webhook.dedupe_ttl_seconds
)


@router.post(
    "/phabricator",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_phabricator_signature)],
)
async def phabricator_webhook(
    request: Request,
    phab_client: PhabricatorClient = Depends(get_phabricator_client),
    authorizer: PhabricatorAuthorizer = Depends(get_phabricator_authorizer),
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
        authorizer=authorizer,
    )
    if detected is None:
        return {"status": "ignored", "reason": "no actionable @hackbot mention"}

    comment, revision_id, bug_id = detected

    run = await api_client.trigger_run(
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
        run.run_id,
        revision_id,
        bug_id,
    )
    return {"status": "triggered", "run_id": run.run_id}


@router.post(
    "/bugzilla",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_bugzilla_webhook_secret)],
)
async def bugzilla_webhook(
    request: Request,
    api_client: HackbotClient = Depends(get_hackbot_client),
) -> dict:
    """Trigger a bug-fix follow-up for a bot-directed ``needinfo?`` change."""
    payload = await request.json()
    if not isinstance(payload, dict):
        return {"status": "ignored", "reason": "payload is not a JSON object"}

    detected = detect_needinfo_request(
        payload,
        bot_login=settings.bugzilla_webhook.bot_login,
    )
    if detected is None:
        return {"status": "ignored", "reason": "no actionable Hackbot needinfo"}
    dedupe_key = f"ni{detected.flag_id}"
    if dedupe_key in _seen_bugzilla_events:
        return {"status": "ignored", "reason": "duplicate delivery"}

    run = await api_client.trigger_run(
        "bug-fix",
        {
            "bug_id": detected.bug_id,
            "bugzilla_needinfo_flag_id": detected.flag_id,
            "comment": detected.comment,
        },
    )
    # Do not consume an event until run creation succeeds; a transient failure
    # must remain retryable by Bugzilla.
    _seen_bugzilla_events[dedupe_key] = True
    log.info(
        "Triggered bug-fix run %s for Bugzilla bug %s from needinfo request",
        run.run_id,
        detected.bug_id,
    )
    return {"status": "triggered", "run_id": run.run_id}
