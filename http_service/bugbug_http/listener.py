#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate your credentials in https://pulseguardian.mozilla.org

Call this script like:
  export PULSE_USER=generated_username
  export PULSE_PASSWORD=generated_username
  # In case you want to hit the live server
  export BUGBUG_HTTP_SERVER=https://bugbug.herokuapp.com
  cd http_service && docker-compose run bugbug-http-service
"""
import logging
import os
import traceback

import requests
from kombu import Connection, Exchange, Queue
from kombu.mixins import ConsumerMixin

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUGBUG_HTTP_SERVER = os.environ.get("BUGBUG_HTTP_SERVER", "http://localhost:8000")
CONNECTION_URL = "amqp://{}:{}@pulse.mozilla.org:5671/?ssl=1"


def _generate_hg_pushes_queue(user):
    return Queue(
        name="queue/{}/pushes".format(user),
        exchange=Exchange("exchange/hgpushes/v2", type="topic", no_declare=True,),
        routing_key="#",
        durable=True,
        auto_delete=True,
    )


class _GenericConsumer(ConsumerMixin):
    def __init__(self, connection, queues, callback):
        self.connection = connection
        self.queues = queues
        self.callback = callback

    def get_consumers(self, Consumer, channel):
        return [Consumer(queues=self.queues, callbacks=[self.callback])]


class ConsumerFactory:
    @staticmethod
    def hg_pushes(user, password, callback):
        connection = Connection(CONNECTION_URL.format(user, password))
        queues = [_generate_hg_pushes_queue(user)]
        consumer = _GenericConsumer(connection, queues, callback)
        return connection, consumer


def _on_message(body, message):
    try:
        branch = body["payload"]["data"]["repo_url"].split("/")[-1]
        rev = body["payload"]["data"]["heads"][0]
        if branch in ["autoland", "try"]:
            url = "{}/push/{}/{}/schedules".format(BUGBUG_HTTP_SERVER, branch, rev)
            response = requests.get(url, headers={"X-Api-Key": "pulse_listener"})
            if response.status_code == 202:
                logger.info("Successfully requested {}/{}".format(branch, rev))
            else:
                logger.warning(
                    "We got status: {} for: {}".format(response.status_code, url)
                )
    except Exception as e:
        traceback.print_tb(e)
    finally:
        message.ack()


def main():
    # Generate user/password in https://pulseguardian.mozilla.org/
    # Set PULSE_USER and PULSE_PASSWORD as env variables
    user = os.environ.get("PULSE_USER")
    password = os.environ.get("PULSE_PASSWORD")
    if user and password:
        connection, consumer = ConsumerFactory.hg_pushes(user, password, _on_message)
        with connection as conn:  # noqa
            consumer.run()
    else:
        logger.warning(
            "The Pulse listener will be skipped unless you define PULSE_USER & PULSE_PASSWORD"
        )


if __name__ == "__main__":
    main()
