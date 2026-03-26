# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import json
from datetime import datetime, timezone
from logging import INFO, basicConfig, getLogger

import google.auth
import requests
from google.auth.transport.requests import Request

basicConfig(level=INFO)
logger = getLogger(__name__)

API_URL_TEMPLATE = "https://logging.googleapis.com/v2/entries:list"
CONTAINER_EXCLUSIONS = ('resource.labels.container_name!="nginx"',)
DEPLOYMENT_CONFIG = {
    "prod": {
        "project": "moz-fx-webservices-high-prod",
        "namespace": "bugbug-prod",
    },
    "dev": {
        "project": "moz-fx-webservices-high-nonprod",
        "namespace": "bugbug-dev",
    },
}
READ_ONLY_SCOPE = "https://www.googleapis.com/auth/logging.read"
PAGE_SIZE = 10000


def parse_rfc3339(value: str) -> str:
    normalized_value = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid RFC3339 timestamp: {value}") from exc

    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_filter(
    base_filter: str,
    start_time: str | None,
    end_time: str | None,
) -> str:
    parts = [f"({base_filter})"]

    if start_time:
        parts.append(f'timestamp >= "{start_time}"')

    if end_time:
        parts.append(f'timestamp <= "{end_time}"')

    return "\n".join(parts)


def get_access_token() -> str:
    credentials, _ = google.auth.default(scopes=[READ_ONLY_SCOPE])

    if not credentials.valid:
        credentials.refresh(Request())

    if not credentials.token:
        raise RuntimeError("Unable to obtain a Google Cloud access token.")

    return credentials.token


def list_entries(
    project: str,
    log_filter: str,
    order_by: str,
    page_token: str | None,
) -> dict:
    access_token = get_access_token()
    response = requests.post(
        API_URL_TEMPLATE,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json={
            "resourceNames": [f"projects/{project}"],
            "filter": log_filter,
            "orderBy": order_by,
            "pageSize": PAGE_SIZE,
            **({"pageToken": page_token} if page_token else {}),
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def get_pod_name(entry: dict) -> str:
    resource_labels = entry.get("resource", {}).get("labels", {})
    return resource_labels.get("pod_name", resource_labels.get("container_name", "-"))


def get_text_payload(entry: dict) -> str:
    if "textPayload" in entry:
        payload = entry["textPayload"]
    elif "jsonPayload" in entry:
        payload = json.dumps(entry["jsonPayload"], sort_keys=True)
    elif "protoPayload" in entry:
        payload = json.dumps(entry["protoPayload"], sort_keys=True)
    else:
        payload = "-"

    return " ".join(str(payload).splitlines()).strip() or "-"


def format_entry(entry: dict) -> str:
    timestamp = parse_rfc3339(
        entry.get("timestamp", datetime.now(timezone.utc).isoformat())
    )
    date_part, time_part = timestamp.rstrip("Z").split("T", 1)

    return "\t".join(
        (
            date_part,
            time_part,
            get_pod_name(entry),
            entry.get("severity", "DEFAULT"),
            get_text_payload(entry),
        )
    )


def get_sort_key(entry: dict) -> str:
    return parse_rfc3339(entry.get("timestamp", datetime.now(timezone.utc).isoformat()))


def get_deployment_config(deployment: str) -> dict[str, str]:
    config = DEPLOYMENT_CONFIG[deployment]
    return {
        "project": config["project"],
        "filter": "\n".join(
            (
                f'resource.labels.namespace_name="{config["namespace"]}"',
                *CONTAINER_EXCLUSIONS,
            )
        ),
    }


def main() -> None:
    description = "Retrieve logs from Google Cloud Logging using the Logging API."
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--deployment",
        default="prod",
        choices=("prod", "dev"),
        help="Deployment to query. Maps to the corresponding GCP project and namespace.",
    )
    parser.add_argument(
        "--start-time",
        type=parse_rfc3339,
        help="Inclusive RFC3339 lower timestamp bound.",
    )
    parser.add_argument(
        "--end-time",
        type=parse_rfc3339,
        help="Inclusive RFC3339 upper timestamp bound.",
    )
    parser.add_argument(
        "--order-by",
        default="timestamp desc",
        choices=("timestamp asc", "timestamp desc"),
        help="Sort order for returned log entries.",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="Output file path. Use '-' to write text to stdout.",
    )

    args = parser.parse_args()
    deployment_config = get_deployment_config(args.deployment)

    log_filter = build_filter(
        deployment_config["filter"],
        args.start_time,
        args.end_time,
    )
    logger.info("Using deployment: %s", args.deployment)
    logger.info("Using project: %s", deployment_config["project"])
    logger.info("Using filter:\n%s", log_filter)

    entries = []
    page_token = None

    page_number = 1
    while True:
        response = list_entries(
            deployment_config["project"],
            log_filter,
            args.order_by,
            page_token,
        )
        page_entries = response.get("entries", [])
        logger.info("Fetched page %s with %s entries", page_number, len(page_entries))
        entries.extend(page_entries)

        page_token = response.get("nextPageToken")
        if not page_token:
            break
        page_number += 1

    entries.sort(key=get_sort_key)

    lines = ["DATE\tHOUR\tPOD_NAME\tSEVERITY\tTEXT_PAYLOAD"]
    lines.extend(format_entry(entry) for entry in entries)
    serialized_output = "\n".join(lines)

    if args.output == "-":
        print(serialized_output)
        return

    with open(args.output, "w") as f:
        f.write(serialized_output)
        f.write("\n")


if __name__ == "__main__":
    main()
