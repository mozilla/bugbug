#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate your credentials in https://pulseguardian.mozilla.org

Call this script like:
  export PULSE_USER=generated_username
  export PULSE_PASSWORD=generated_username
  ./http_service/pulse.py
  # Or alternatively
  cd http_service && docker-compose run bugbug-http-service-rq-dasboard
"""
import logging
import os

from pulse.consumers import ConsumerFactory

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _on_message(body, message):
    logger.info(body)
    message.ack()


def main():
    # Generate user/password in https://pulseguardian.mozilla.org/
    # Set PULSE_USER and PULSE_PASSWORD as env variables
    user = os.environ["PULSE_USER"]
    password = os.environ["PULSE_PASSWORD"]
    connection, consumer = ConsumerFactory.hg_pushes(user, password, _on_message)
    with connection as conn:  # noqa
        consumer.run()


if __name__ == "__main__":
    main()
