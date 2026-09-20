"""Inbound Slack interactivity receiver: clicks on the messages hackbot posts.

``docs/hackbot/api.md`` covers the endpoint, the delivery shape and the Slack-app
config; ``docs/hackbot/security.md`` covers the signature and what it does not
prove.
"""

import logging

from fastapi import APIRouter, Depends, Request, Response, status

from app.auth import require_slack_signature
from app.routers.webhooks import get_hackbot_client
from app.slack_webhook import BlockActionsEvent, Container

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
    form = await request.form()
    event = BlockActionsEvent.model_validate_json(form["payload"])
    log.info(
        "Slack action: %s by %s (%s)",
        event.trigger_id,
        event.user.username,
        event.user.id,
    )

    if len(event.actions) != 1:
        raise ValueError(
            "Expected exactly one action in a click delivery, got %d"
            % len(event.actions)
        )

    action = event.actions[0]
    match action.value.type:
        case "start_agent_run":
            client = get_hackbot_client()
            run = await client.trigger_run(
                action.value.agent_name,
                action.value.params,
                dedupe_key=button_dedupe_key(event.container),
            )
            log.info(
                "Slack action by %s on %s/%s -> run %s (%s)",
                event.user.id,
                event.container.channel_id,
                event.container.message_ts,
                run.run_id,
                run.status,
            )
        case _:
            raise ValueError("Unsupported action type: %s" % action.value.type)

    return Response(status_code=status.HTTP_200_OK)


def button_dedupe_key(container: Container) -> str:
    """Key a run on the message the button sits on, so the button works once.

    Channel plus ``ts`` is how Slack identifies a message; ``ts`` alone is only
    unique within a channel.
    """
    return f"slack:{container.channel_id}:{container.message_ts}"
