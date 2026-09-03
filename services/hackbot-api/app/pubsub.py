import asyncio
import json
import logging
from functools import lru_cache

from google.cloud import pubsub_v1

from app.config import settings

log = logging.getLogger(__name__)

# Bump when the attribute set or payload shape changes incompatibly, so
# consumers can guard on `attributes.schema_version` if they need to.
EVENT_SCHEMA_VERSION = "1"


@lru_cache(maxsize=1)
def _publisher() -> pubsub_v1.PublisherClient:
    return pubsub_v1.PublisherClient()


def _topic_path(topic: str) -> str:
    if not settings.gcp_project:
        raise RuntimeError("gcp_project not configured")
    return _publisher().topic_path(settings.gcp_project, topic)


def _publish_sync(topic: str, data: bytes, attributes: dict[str, str]) -> str:
    future = _publisher().publish(_topic_path(topic), data, **attributes)
    return future.result()


def _build_event(
    event_type: str, payload: dict, attributes: dict[str, str]
) -> tuple[bytes, dict[str, str]]:
    """Serialize one domain event into (body, attributes).

    `event_type` (dotted ``<domain>.<action>``, e.g. ``run.completed``) and
    the caller's routing attributes are merged with the schema version into the
    attribute map. Pub/Sub subscription filters can only match on *attributes*,
    never the body, so every key a consumer might filter by has to live here;
    the JSON body carries the fuller payload for consumers that need more than
    the filter keys. Pure/synchronous so the attribute wiring is unit-testable
    without touching the network.
    """
    all_attrs = {
        "event_type": event_type,
        "schema_version": EVENT_SCHEMA_VERSION,
        **attributes,
    }
    return json.dumps(payload).encode("utf-8"), all_attrs


async def _publish_event(
    topic: str, event_type: str, payload: dict, attributes: dict[str, str]
) -> None:
    """Publish one domain event to ``topic``. Failures are logged, not raised.

    The publish is best-effort by design: the Run row is already durably
    persisted before any event is published, so a lost publish means a
    delayed/missed downstream reaction, not lost primary state.
    """
    data, all_attrs = _build_event(event_type, payload, attributes)
    try:
        await asyncio.to_thread(_publish_sync, topic, data, all_attrs)
    except Exception:
        log.exception("Failed to publish %s event", event_type)


async def publish_run_completed(run_id: str, agent: str, status: str) -> None:
    """Publish a ``run.completed`` event to the run-domain topic.

    Topics follow a per-domain convention, ``<domain>-events`` (the GCP project
    is hackbot-only, so no ``hackbot-`` prefix); this is the agent-run domain
    (``settings.run_events_topic``, default ``agent-run-events``). A new domain
    (e.g. subscriber notifications, or
    inter-service coordination events) gets its OWN topic plus a small wrapper
    like this one, rather than overloading this stream — keeping IAM, retention,
    and schema separable per domain as the system grows. Finer selection within
    a domain is done by consumers via subscription filters on the attributes
    set here (`event_type`, `agent`, `status`).
    """
    await _publish_event(
        settings.run_events_topic,
        event_type="run.completed",
        payload={"run_id": run_id, "agent": agent, "status": status},
        attributes={"agent": agent, "status": status},
    )
