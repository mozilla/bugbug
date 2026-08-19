"""Inbound Slack interactivity receiver: clicks on the messages hackbot posts.

A message recorded with buttons (``hackbot_runtime.actions.slack.button``) is
posted as Block Kit by the apply step; when someone clicks one, Slack POSTs the
click here. Authenticated by Slack's HMAC signature rather than the ``X-API-Key``
the other routes use, so this lives on its own router without ``require_api_key``,
the same way the Phabricator receiver does.

Slack app setup (one URL for the whole app, no new OAuth scopes, no reinstall):

- Interactivity & Shortcuts -> Interactivity: on
- Request URL: ``https://<hackbot-api-host>/slack/interactions``
- ``SLACK_SIGNING_SECRET`` in this service's env, from Basic Information ->
  App Credentials. Until it is set every delivery is rejected with a 401.

Two constraints shape what may go in this route. Slack expects a response within
**3 seconds** and shows the clicker an error if it does not arrive, so real work
belongs off this request (publish an event, as ``run.completed`` does, and answer
the message afterwards through ``response_url`` or ``chat.update``). And Slack
retries a non-2xx delivery, so a payload this cannot act on is answered 200 and
logged, not 4xx/5xx: a retry of it would fail identically while the person who
clicked watches it fail.
"""

import logging

from fastapi import APIRouter, Depends, Request, Response, status
from hackbot_runtime.actions.slack import BUTTON_KINDS

from app.auth import require_slack_signature
from app.slack_webhook import parse_interaction

log = logging.getLogger(__name__)

router = APIRouter(prefix="/slack")


@router.post(
    "/interactions",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_slack_signature)],
)
async def slack_interactions(request: Request) -> Response:
    # Already read (and cached) by the signature dependency: the signature covers
    # the bytes as sent, and the form body is parsed from those same bytes.
    click = parse_interaction(await request.body())
    if click is None:
        # Not a click this can act on. Already logged with the reason.
        return Response(status_code=status.HTTP_200_OK)

    if click.kind not in BUTTON_KINDS:
        # A button whose kind no longer has a receiver: an older message still in
        # a channel's history, or a kind retired without retiring its buttons.
        log.warning(
            "Slack: no receiver for button kind %r (from user %s in channel %s)",
            click.kind,
            click.user_id,
            click.channel_id,
        )
        return Response(status_code=status.HTTP_200_OK)

    log.info(
        "Slack: %s clicked by %s (%s) in channel %s on message %s, args=%s",
        click.kind,
        click.user_name or "unknown",
        click.user_id,
        click.channel_id,
        click.message_ts,
        click.args,
    )

    # ACTION HANDLING GOES HERE
    #
    # The click is authenticated and parsed at this point; nothing acts on it yet.
    # What belongs here, and what it needs from `click`:
    #
    # 1. Authorize the clicker. `click.user_id` is a Slack id, not an identity this
    #    service trusts: resolve it to an email with `users.info` (needs the
    #    `users:read` / `users:read.email` scopes, so a reinstall) and require the
    #    same @mozilla.com bar the UI applies. Check `click.team_id` is the expected
    #    workspace too. Fail closed.
    # 2. Make it at-most-once. Slack retries deliveries and people double-click, so
    #    the effect has to be keyed on something stable, e.g. (message_ts, kind),
    #    in a row that only one caller can transition out of pending.
    # 3. Hand the work off rather than doing it here, to stay inside the 3-second
    #    budget: publish the click and let a push subscription act on it (see
    #    `app/pubsub.py`), which is how run completions already reach their handler.
    # 4. Answer the person who clicked, twice: strip the buttons immediately via
    #    `click.response_url` so a second click has nothing to hit, then report the
    #    outcome from the worker with `chat.update` on the message the posted action
    #    recorded (`{"channel", "ts"}` in its result).
    #
    # For `trigger_bug_fix` specifically, `click.args` carries `bug_id` and the
    # `run_id` of the triage run that proposed the fix, which is everything a
    # `POST /agents/bug-fix/runs` needs (attributed to the clicker through
    # `X-On-Behalf-Of`).

    return Response(status_code=status.HTTP_200_OK)
