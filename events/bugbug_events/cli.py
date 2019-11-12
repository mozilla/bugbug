# -*- coding: utf-8 -*-
import argparse
import os

import structlog
from libmozevent import taskcluster_config

from bugbug_events.workflow import Events

logger = structlog.get_logger(__name__)


def parse_cli():
    """
    Setup CLI options parser
    """
    parser = argparse.ArgumentParser(description="Mozilla BugBug events")
    parser.add_argument(
        "--taskcluster-secret",
        help="Taskcluster Secret path",
        default=os.environ.get("TASKCLUSTER_SECRET"),
    )
    parser.add_argument("--taskcluster-client-id", help="Taskcluster Client ID")
    parser.add_argument("--taskcluster-access-token", help="Taskcluster Access token")
    return parser.parse_args()


def main():
    args = parse_cli()
    taskcluster_config.auth(args.taskcluster_client_id, args.taskcluster_access_token)
    taskcluster_config.load_secrets(
        args.taskcluster_secret,
        "events",
        required=("admins", "PHABRICATOR"),
        existing=dict(admins=["babadie@mozilla.com", "mcastelluccio@mozilla.com"]),
    )

    events = Events()
    events.run()


if __name__ == "__main__":
    main()
