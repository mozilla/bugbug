"""Trigger a build-repair run for a real failing build task and email the result.

Drives the listener's normal path (trigger the agent via hackbot-api, poll the
run to completion, send the notification) from a synthetic pulse message, so you
can test on a real failure without waiting for a live one.

Credentials and settings are read from the environment / ``.env`` (see the
service README): HACKBOT_API_URL, HACKBOT_API_KEY, SENDGRID_API_KEY,
NOTIFICATION_SENDER, and NOTIFICATION_OVERRIDE_EMAIL. Always set
NOTIFICATION_OVERRIDE_EMAIL to your own address so the run emails you and not the
real developer; the script refuses to run otherwise.

Usage (from the service directory, with the env exported):

    uv run --package hackbot-pulse-listener python scripts/send_test_run.py \
        <TASK_ID> --label build-linux64/opt [--project autoland] [--force]

Find a task id on Treeherder: a red build ("B") job -> Task inspector -> taskId.
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

from app import consumer
from app.config import settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task_id", help="Taskcluster task id of a failed build job")
    parser.add_argument(
        "--label",
        default="build-linux64/opt",
        help="Build task label; must contain 'build' and not 'test'",
    )
    parser.add_argument("--project", default="autoland", help="Taskcluster project tag")
    parser.add_argument(
        "--created-for", default="", help="createdForUser: the pushing developer email"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the regression gate so a run always triggers",
    )
    args = parser.parse_args()

    if not settings.notification_override_email:
        parser.error(
            "Set NOTIFICATION_OVERRIDE_EMAIL to your address so the test emails you, "
            "not the real developer."
        )

    if args.force:
        consumer.regression.is_new_build_failure = lambda *a, **k: True

    if args.project not in settings.watched_repos_set:
        settings.watched_repos = f"{settings.watched_repos},{args.project}"

    msg = {
        "status": {"taskId": args.task_id},
        "task": {
            "tags": {
                "kind": "build",
                "project": args.project,
                "label": args.label,
                "createdForUser": args.created_for,
            }
        },
    }

    with ThreadPoolExecutor(max_workers=4) as executor:
        run_id = consumer.process(msg, executor)
        if run_id is None:
            print(
                "No run triggered (filtered out, deduped, or DRY_RUN). Check "
                "WATCHED_REPOS/DRY_RUN, or pass --force to skip the regression gate.",
                file=sys.stderr,
            )
            return 1
        print(
            f"Triggered run {run_id}; polling until it finishes and emailing "
            f"{settings.notification_override_email} (this can take several minutes)..."
        )
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
