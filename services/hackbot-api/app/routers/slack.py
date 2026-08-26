"""Inbound Slack interactivity receiver: clicks on the messages hackbot posts.

``docs/hackbot/api.md`` covers the endpoint, the delivery shape and the Slack-app
config; ``docs/hackbot/security.md`` covers the signature and what it does not
prove.
"""

import logging

from fastapi import APIRouter, Depends, Request, Response, status

from app.auth import require_slack_signature
from app.slack_webhook import parse_interaction

log = logging.getLogger(__name__)

# Under `/webhooks` with the Phabricator receiver, since both are signature-verified
# inbound deliveries, and one level deeper because Slack configures a Request URL per
# feature: Event Subscriptions and Slash Commands are separate URLs with their own
# payload shapes, and they belong beside this one rather than sharing it.
router = APIRouter(prefix="/webhooks/slack")


@router.post(
    "/interactions",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_slack_signature)],
)
async def slack_interactions(request: Request) -> Response:
    # Already read (and cached) by the signature dependency: the signature covers
    # the bytes as sent, and the form body is parsed from those same bytes.
    click = parse_interaction(await request.body())

    action = click.actions[0]
    log.info(
        "Slack: %s clicked by %s (%s) in channel %s on message %s, value=%s",
        action.action_id,
        click.user.username or "unknown",
        click.user.id,
        click.channel.id,
        click.message.ts,
        action.value,
    )

    # ACTION HANDLING GOES HERE
    #
    # The click is authenticated and parsed; nothing acts on it. What is still
    # missing, in the order it has to happen (the reasoning is in
    # `docs/hackbot/api.md` and `docs/hackbot/security.md`):
    #
    # 1. Dispatch on `action.action_id` against the ids that have a receiver, and
    #    answer 200 for one that does not.
    # 2. Authorize the clicker from `click.user.id`, and check the workspace, which
    #    means adding the payload's `team` to the model when this lands.
    # 3. Make the effect at-most-once, keyed on something stable such as
    #    (`click.message.ts`, `action.action_id`).
    # 4. Publish the click and act on it off this request (see `app/pubsub.py`).
    # 5. Answer through `click.response_url`, then `chat.update` the message.

    return Response(status_code=status.HTTP_200_OK)
