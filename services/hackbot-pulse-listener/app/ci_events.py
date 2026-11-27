# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Forward try task terminal events to the Hackbot API's persisted CI waits."""

import json
from functools import lru_cache

from app.config import settings


@lru_cache(maxsize=1)
def publisher():
    from google.cloud import pubsub_v1

    return pubsub_v1.PublisherClient()


def forward(body: dict) -> None:
    if not settings.ci_events_topic or settings.dry_run:
        return
    if (body.get("task") or {}).get("tags", {}).get("project") != "try":
        return
    status = body.get("status") or {}
    group = status.get("taskGroupId")
    if not group or status.get("state") not in {"completed", "failed", "exception"}:
        return
    publisher().publish(
        settings.ci_events_topic,
        json.dumps(
            {"task_id": status.get("taskId"), "state": status["state"]}
        ).encode(),
        task_group_id=group,
    ).result(timeout=30)
