"""Inbound webhooks that trigger Hackbot runs."""

import asyncio
import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from hackbot_client import HackbotClient
from phabricator_client import PhabricatorClient
from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    require_bugzilla_webhook_secret,
    require_github_signature,
    require_phabricator_signature,
)
from app.bugzilla_authorization import BugzillaAuthorizer
from app.bugzilla_webhook import detect_needinfo_request
from app.config import settings
from app.database.connection import get_db
from app.github import GitHubApp
from app.github_authorization import GitHubAuthorizer
from app.github_webhook import (
    DELIVERY_DEADLINE_SECONDS,
    GitHubMention,
    detect_mention,
    resolve_head_sha,
)
from app.hackbot_client import get_hackbot_client
from app.phabricator_authorization import PhabricatorAuthorizer
from app.phabricator_webhook import (
    detect_mention_and_revision,
    follow_up_agent,
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
    phab_client: Annotated[PhabricatorClient, Depends(get_phabricator_client)],
) -> PhabricatorAuthorizer:
    """Dependency: lazily create the app-scoped authorizer and its member cache."""
    authorizer = getattr(request.app.state, "phabricator_authorizer", None)
    if authorizer is None:
        authorizer = PhabricatorAuthorizer(
            phab_client, settings.webhook.authorized_group
        )
        request.app.state.phabricator_authorizer = authorizer
    return authorizer


def get_bugzilla_authorizer(request: Request) -> BugzillaAuthorizer:
    """Dependency: lazily create the app-scoped authorizer and its user cache."""
    authorizer = getattr(request.app.state, "bugzilla_authorizer", None)
    if authorizer is None:
        authorizer = BugzillaAuthorizer(
            settings.bugzilla_api_url,
            settings.bugzilla_api_key,
            settings.bugzilla_webhook.authorized_group,
        )
        request.app.state.bugzilla_authorizer = authorizer
    return authorizer


def get_github_app(request: Request) -> GitHubApp:
    """Dependency: lazily create the app-scoped App client and its token cache."""
    github_app = getattr(request.app.state, "github_app", None)
    if github_app is None:
        if settings.github is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="GitHub App is not configured",
            )
        # Its calls run inside a webhook delivery, which GitHub abandons after
        # 10 seconds. Each lookup is bounded; without retries, a 5xx also fails
        # at once instead of spending that budget on githubkit's backoff.
        github_app = GitHubApp(settings.github, auto_retry=False)
        request.app.state.github_app = github_app
    return github_app


def get_github_authorizer(request: Request) -> GitHubAuthorizer:
    """Dependency: lazily create the app-scoped authorizer and its role cache."""
    authorizer = getattr(request.app.state, "github_authorizer", None)
    if authorizer is None:
        github_app = get_github_app(request)
        authorizer = GitHubAuthorizer(
            github_app,
            settings.github.allowed_roles,
            settings.github.bot_login,
        )
        request.app.state.github_authorizer = authorizer
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
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    payload = await request.json()

    action = payload.get("action") or {}
    if action.get("test"):
        # Phabricator's "test" ping when a webhook is created/edited.
        log.info("Phabricator webhook ignored: test ping")
        return

    obj = payload.get("object") or {}
    if obj.get("type") != "DREV":
        log.warning("Phabricator webhook ignored: not a revision")
        return

    object_phid = obj.get("phid")
    triggering = triggering_transaction_phids(payload)
    if not object_phid or not triggering:
        log.warning("Phabricator webhook ignored: no revision or transactions")
        return

    detected = await detect_mention_and_revision(
        phab_client,
        settings.webhook,
        object_phid,
        triggering,
        authorizer=authorizer,
    )
    if detected is None:
        log.info("Phabricator webhook ignored: no actionable @hackbot mention")
        return

    agent = await follow_up_agent(db, detected.revision_id)

    # The anchor transaction identifies the submission, so a retried delivery
    # is answered with the run the first delivery created, on any instance.
    run = await api_client.trigger_run(
        agent,
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
        return
    log.info(
        "Triggered %s run %s for D%s (bug %s) from @hackbot mention (%s)",
        agent,
        run.run_id,
        detected.revision_id,
        detected.bug_id,
        detected.anchor_phid,
    )


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


@router.post(
    "/github",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_github_signature)],
)
async def github_webhook(
    request: Request,
    api_client: Annotated[HackbotClient, Depends(get_hackbot_client)],
    github_app: Annotated[GitHubApp, Depends(get_github_app)],
    authorizer: Annotated[GitHubAuthorizer, Depends(get_github_authorizer)],
    x_github_event: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
) -> dict:
    """Trigger a bug-fix run for an ``@hackbot`` mention on a pull request."""
    if x_github_event == "ping":
        # Sent once when the App's webhook is created or its URL changes.
        return {"status": "ignored", "reason": "ping"}

    payload = await request.json()
    if not isinstance(payload, dict):
        return {"status": "ignored", "reason": "payload is not a JSON object"}

    mention = detect_mention(
        x_github_event, payload, mention_token=settings.github.mention_token
    )
    if mention is None:
        return {"status": "ignored", "reason": "no actionable mention"}

    try:
        async with asyncio.timeout(DELIVERY_DEADLINE_SECONDS):
            return await _trigger_github_mention(
                mention, api_client, github_app, authorizer, x_github_delivery
            )
    except TimeoutError:
        log.warning(
            "Timed out handling GitHub delivery %s for %s#%s (%s)",
            x_github_delivery,
            mention.full_name,
            mention.pr_number,
            mention.dedupe_key,
        )
        # A 5xx marks the delivery failed in the App's settings, where it can
        # be redelivered. If the deadline hit while the run was being created,
        # it may exist already; the redelivery has the same comment ID, so it
        # resolves to that run rather than starting a second.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Timed out handling the delivery",
        ) from None


async def _trigger_github_mention(
    mention: GitHubMention,
    api_client: HackbotClient,
    github_app: GitHubApp,
    authorizer: GitHubAuthorizer,
    x_github_delivery: str | None,
) -> dict:
    """Authorize ``mention``, resolve its PR's head and trigger the run."""
    if not await authorizer.is_authorized(
        mention.installation_id, mention.owner, mention.repo, mention.sender
    ):
        log.info(
            "Ignored GitHub mention on %s#%s (delivery %s): %s is not authorized",
            mention.full_name,
            mention.pr_number,
            x_github_delivery,
            mention.sender_login,
        )
        return {"status": "ignored", "reason": "unauthorized user"}

    head_sha = mention.head_sha
    if head_sha is None:
        head_sha = await resolve_head_sha(github_app, mention)
        if head_sha is None:
            return {"status": "ignored", "reason": "pull request is not open"}

    # The comment or review ID identifies the request, so a redelivery (which
    # GitHub sends only on request) is answered with the run it first created.
    run = await api_client.trigger_run(
        "bug-fix",
        {
            "github_repo": mention.full_name,
            "github_pr_number": mention.pr_number,
            "github_head_sha": head_sha,
            "comment": mention.comment,
        },
        dedupe_key=mention.dedupe_key,
    )
    if not run.is_new:
        log.info(
            "Duplicate GitHub delivery %s for %s#%s (%s) resolved to run %s",
            x_github_delivery,
            mention.full_name,
            mention.pr_number,
            mention.dedupe_key,
            run.run_id,
        )
        return {
            "status": "ignored",
            "reason": "duplicate delivery",
            "run_id": run.run_id,
        }
    log.info(
        "Triggered bug-fix run %s for %s#%s from %s mention by %s (%s)",
        run.run_id,
        mention.full_name,
        mention.pr_number,
        settings.github.mention_token,
        mention.sender_login,
        mention.dedupe_key,
    )
    return {"status": "triggered", "run_id": run.run_id}
