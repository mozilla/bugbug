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
from pulse.consumers import ConsumerFactory

logging.basicConfig()
logger = logging.getLogger()
logger.setLevel(logging.INFO)

BUGBUG_HTTP_SERVER = os.environ.get("BUGBUG_HTTP_SERVER", "http://localhost:8000")


def _on_message(body, message):
    try:
        branch = body["payload"]["data"]["repo_url"].split("/")[-1]
        rev = body["payload"]["data"]["heads"][0]
        if branch in ["autoland", "try"]:
            url = "{}/push/{}/{}/schedules".format(BUGBUG_HTTP_SERVER, branch, rev)
            response = requests.get(url, headers={"X-Api-Key": "pulse_listener"})
            if response.status_code == 202:
                logger.info("Sueccesfully requested {}/{}".format(branch, rev))
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
