"""Inbound webhooks that trigger Hackbot runs."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from hackbot_client import HackbotClient
from phabricator_client import PhabricatorClient
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler

from app.auth import (
    require_bugzilla_webhook_secret,
    require_phabricator_signature,
)
from app.bugzilla_authorization import AUTHORIZED_GROUP_NAME, BugzillaAuthorizer
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
from app.slack.app import build_request_handler

log = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks")


def get_phabricator_client() -> PhabricatorClient:
    """Dependency: a Conduit client built from the service's Phabricator config."""
    return PhabricatorClient(settings.phabricator)


def get_slack_handler(request: Request) -> AsyncSlackRequestHandler:
    """Dependency: lazily create the app-scoped Bolt handler."""
    handler = getattr(request.app.state, "slack_handler", None)
    if handler is None:
        handler = build_request_handler()
        request.app.state.slack_handler = handler
    return handler


def get_hackbot_client() -> HackbotClient:
    """Dependency: a client for triggering runs over the public hackbot API."""
    return HackbotClient(
        base_url=settings.hackbot_api_url,
        api_key=settings.external_api_key,
    )


@router.post("/slack")
async def slack_webhook(
    request: Request,
    slack_handler: Annotated[AsyncSlackRequestHandler, Depends(get_slack_handler)],
    api_client: Annotated[HackbotClient, Depends(get_hackbot_client)],
) -> Response:
    """Every interaction Slack sends this app, whatever kind it is."""
    return await slack_handler.handle(
        request, addition_context_properties={"hackbot_client": api_client}
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


def get_bugzilla_authorizer(request: Request) -> BugzillaAuthorizer:
    """Dependency: lazily create the app-scoped authorizer and its user cache."""
    authorizer = getattr(request.app.state, "bugzilla_authorizer", None)
    if authorizer is None:
        authorizer = BugzillaAuthorizer(
            settings.bugzilla_api_url,
            settings.bugzilla_api_key,
            AUTHORIZED_GROUP_NAME,
        )
        request.app.state.bugzilla_authorizer = authorizer
    return authorizer


@router.post(
    "/phabricator",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_phabricator_signature)],
)
async def phabricator_webhook(
    request: Request,
    phab_client: Annotated[PhabricatorClient, Depends(get_phabricator_client)],
    authorizer: Annotated[PhabricatorAuthorizer, Depends(get_phabricator_authorizer)],
    api_client: Annotated[HackbotClient, Depends(get_hackbot_client)],
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

    detected = await detect_mention_and_revision(
        phab_client,
        settings.webhook,
        object_phid,
        triggering,
        authorizer=authorizer,
    )
    if detected is None:
        return {"status": "ignored", "reason": "no actionable @hackbot mention"}

    # The anchor transaction identifies the submission, so a retried delivery
    # is answered with the run the first delivery created, on any instance.
    run = await api_client.trigger_run(
        "bug-fix",
        {
            "bug_id": detected.bug_id,
            "revision_id": detected.revision_id,
            "comment": detected.comment,
        },
        dedupe_key=f"phab-txn:{detected.anchor_phid}",
    )
    if not run.is_new:
        log.info(
            "Duplicate Phabricator delivery for D%s (%s) resolved to run %s",
            detected.revision_id,
            detected.anchor_phid,
            run.run_id,
        )
        return {
            "status": "ignored",
            "reason": "duplicate delivery",
            "run_id": run.run_id,
        }
    log.info(
        "Triggered bug-fix run %s for D%s (bug %s) from @hackbot mention (%s)",
        run.run_id,
        detected.revision_id,
        detected.bug_id,
        detected.anchor_phid,
    )
    return {"status": "triggered", "run_id": run.run_id}


@router.post(
    "/bugzilla",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_bugzilla_webhook_secret)],
)
async def bugzilla_webhook(
    request: Request,
    api_client: Annotated[HackbotClient, Depends(get_hackbot_client)],
    authorizer: Annotated[BugzillaAuthorizer, Depends(get_bugzilla_authorizer)],
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

    if not await authorizer.is_authorized(detected.user_login):
        log.info(
            "Ignored Bugzilla needinfo webhook for bug %s: %s is not authorized",
            detected.bug_id,
            detected.user_login,
        )
        return {"status": "ignored", "reason": "unauthorized user"}

    run = await api_client.trigger_run(
        "bug-fix",
        {
            "bug_id": detected.bug_id,
            "bugzilla_needinfo_flag_id": detected.flag_id,
            "comment": detected.comment,
        },
        dedupe_key=f"ni{detected.flag_id}",
    )
    if not run.is_new:
        log.info(
            "Duplicate Bugzilla delivery for bug %s (flag: %s) resolved to run %s",
            detected.bug_id,
            detected.flag_id,
            run.run_id,
        )
        return {
            "status": "ignored",
            "reason": "duplicate delivery",
            "run_id": run.run_id,
        }
    log.info(
        "Triggered bug-fix run %s for Bugzilla bug %s from needinfo request (flag: %s)",
        run.run_id,
        detected.bug_id,
        detected.flag_id,
    )
    return {"status": "triggered", "run_id": run.run_id}
