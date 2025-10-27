#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate your credentials in https://pulseguardian.mozilla.org

Call this script like:
  export PULSE_USER=generated_username
  export PULSE_PASSWORD=generated_username
  # In case you want to hit the live server
  export BUGBUG_HTTP_SERVER=https://bugbug.moz.tools
  cd http_service && docker-compose run bugbug-http-service
"""

import logging
import os
import traceback

import requests
from kombu import Connection, Exchange, Queue
from kombu.mixins import ConsumerMixin

from bugbug_http.sentry import setup_sentry

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel(logging.INFO)

PORT = os.environ.get("PORT", 8000)
BUGBUG_HTTP_SERVER = os.environ.get("BUGBUG_HTTP_SERVER", f"http://localhost:{PORT}")
CONNECTION_URL = "amqp://{}:{}@pulse.mozilla.org:5671/?ssl=1"

if os.environ.get("SENTRY_DSN"):
    setup_sentry(dsn=os.environ.get("SENTRY_DSN"))


class _GenericConsumer(ConsumerMixin):
    def __init__(self, connection, queues, callback):
        self.connection = connection
        self.queues = queues
        self.callback = callback

    def get_consumers(self, Consumer, channel):
        return [Consumer(queues=self.queues, callbacks=[self.callback])]


class HgPushesConsumer:
    def __init__(self, user, password, callback):
        self.connection = Connection(CONNECTION_URL.format(user, password))
        self.queues = [
            Queue(
                name="queue/{}/pushes".format(user),
                exchange=Exchange(
                    "exchange/hgpushes/v2",
                    type="topic",
                    no_declare=True,
                ),
                routing_key="#",
                durable=True,
                auto_delete=True,
            )
        ]
        self.consumer = _GenericConsumer(self.connection, self.queues, callback)

    def __enter__(self):
        return self.consumer

    def __exit__(self, type, value, traceback):
        self.connection.close()


def _on_message(body, message):
    try:
        # Only act on messages describing a push that introduced commits on a repository.
        # Skip repository creations and obsolescence markers additions.
        if body["payload"]["type"] != "changegroup.1":
            return

        branch = body["payload"]["data"]["repo_url"].split("/")[-1]
        rev = body["payload"]["data"]["heads"][0]

        if branch in ["autoland", "try"]:
            user = body["payload"]["data"]["pushlog_pushes"][0]["user"]
            if user in ("reviewbot", "wptsync@mozilla.com"):
                return

            url = "{}/push/{}/{}/schedules".format(BUGBUG_HTTP_SERVER, branch, rev)
            response = requests.get(url, headers={"X-Api-Key": "pulse_listener"})
            if response.status_code == 202:
                logger.info("Successfully requested {}/{}".format(branch, rev))
            else:
                logger.warning(
                    "We got status: {} for: {}".format(response.status_code, url)
                )
    except Exception:
        logger.warning(body)
        traceback.print_exc()
    finally:
        message.ack()


def main():
    # Generate user/password in https://pulseguardian.mozilla.org/
    # Set PULSE_USER and PULSE_PASSWORD as env variables
    user = os.environ.get("PULSE_USER")
    password = os.environ.get("PULSE_PASSWORD")
    if user and password:
        with HgPushesConsumer(user, password, _on_message) as consumer:
            consumer.run()
    else:
        logger.warning(
            "The Pulse listener will be skipped unless you define PULSE_USER & PULSE_PASSWORD"
        )


if __name__ == "__main__":
    main()
