"""Lookups in Firefox's ETP exception list (`url-classifier-exceptions`).

Used after the run to flag existing entries that overlap the proposal, and by
the agent itself through the `find_etp_exceptions` tool.
"""

import json
import logging
import re
from typing import Any

import requests
from claude_agent_sdk import McpServerConfig, create_sdk_mcp_server, tool

from .mcp_servers import REMOTE_SETTINGS_SERVER

logger = logging.getLogger("autowebcompat-etp")

EXCEPTIONS_URL = (
    f"{REMOTE_SETTINGS_SERVER}/buckets/main/collections/"
    "url-classifier-exceptions/records"
)

EXCEPTIONS_SERVER_NAME = "etp-exceptions"
FIND_EXCEPTIONS_TOOL = f"mcp__{EXCEPTIONS_SERVER_NAME}__find_etp_exceptions"

# The fields worth showing for an entry; the rest is Remote Settings metadata.
ENTRY_FIELDS = (
    "id",
    "bugIds",
    "category",
    "urlPattern",
    "topLevelUrlPattern",
    "classifierFeatures",
    "filter_expression",
    "isPrivateBrowsingOnly",
    "filterContentBlockingCategories",
)


def pattern_host(pattern: str) -> str:
    """The host of a match pattern, without a leading `*.` wildcard."""
    match = re.match(r"^[^:]+://(?:\*\.)?([^/:]+)", pattern)
    return match.group(1) if match else ""


def to_host(value: str) -> str:
    """The host of a match pattern, URL or bare host name."""
    if "://" in value:
        return pattern_host(value)
    return value.split("/", 1)[0].removeprefix("*.")


def hosts_overlap(a: str, b: str) -> bool:
    return a == b or a.endswith(f".{b}") or b.endswith(f".{a}")


def fetch_exceptions() -> list[dict[str, Any]]:
    """All entries of the live collection. Raises on network or format errors."""
    response = requests.get(EXCEPTIONS_URL, timeout=60)
    response.raise_for_status()
    return response.json()["data"]


def summarize(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: entry[key] for key in ENTRY_FIELDS if key in entry}


def matching_exceptions(
    existing: list[dict[str, Any]],
    resource_hosts: list[str],
    site_hosts: list[str] | None = None,
    bug_id: int | None = None,
) -> list[dict[str, Any]]:
    """Entries covering any of the resource hosts on any of the sites.

    An entry matches if its `urlPattern` host overlaps a resource host and it is
    global or its `topLevelUrlPattern` host overlaps a site host (any site if
    `site_hosts` is empty), or if it lists `bug_id`.
    """
    matches = []
    for entry in existing:
        entry_host = pattern_host(entry.get("urlPattern", ""))
        entry_top = entry.get("topLevelUrlPattern")
        covers_resource = any(hosts_overlap(entry_host, h) for h in resource_hosts)
        covers_site = (
            not site_hosts
            or entry_top is None
            or any(hosts_overlap(pattern_host(entry_top), h) for h in site_hosts)
        )
        for_bug = bug_id is not None and str(bug_id) in entry.get("bugIds", [])
        if (covers_resource and covers_site) or for_bug:
            matches.append(summarize(entry))
    return matches


def find_existing_exceptions(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Existing collection entries that cover the same resource and site.

    Returns None if the collection couldn't be fetched.
    """
    if not records:
        return []
    try:
        existing = fetch_exceptions()
    except requests.RequestException, KeyError, ValueError:
        logger.exception("could not fetch the existing ETP exceptions")
        return None

    matches = []
    for record in records:
        top = record.get("topLevelUrlPattern")
        for match in matching_exceptions(
            existing,
            [pattern_host(record["urlPattern"])],
            [pattern_host(top)] if top else None,
        ):
            if match not in matches:
                matches.append(match)
    return matches


@tool(
    "find_etp_exceptions",
    "Look up existing entries in Firefox's ETP exception list (the live "
    "url-classifier-exceptions Remote Settings collection). Returns every "
    "entry whose urlPattern covers one of `hosts` and that is global or "
    "scoped to one of `sites`, plus every entry listing `bug_id`. Each entry "
    "has its category (baseline/convenience), classifierFeatures and "
    "filter_expression (the Firefox versions it applies to).",
    {
        "type": "object",
        "properties": {
            "hosts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Resource hosts to look up, as host names, URLs or "
                    "match patterns, e.g. `platform.linkedin.com`."
                ),
            },
            "sites": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Top-level sites to limit the lookup to, e.g. "
                    "`www.example.com`. Omit to match any site."
                ),
            },
            "bug_id": {
                "type": "integer",
                "description": "Also return entries filed for this bug.",
            },
        },
        "additionalProperties": False,
    },
)
async def find_etp_exceptions(args: dict) -> dict:
    hosts = [to_host(h) for h in args.get("hosts", [])]
    sites = [to_host(s) for s in args.get("sites", [])]
    bug_id = args.get("bug_id")
    if not hosts and bug_id is None:
        return {
            "content": [{"type": "text", "text": "Pass `hosts`, `bug_id` or both."}],
            "is_error": True,
        }
    try:
        existing = fetch_exceptions()
    except (requests.RequestException, KeyError, ValueError) as exc:
        return {
            "content": [
                {"type": "text", "text": f"Could not fetch the exception list: {exc}"}
            ],
            "is_error": True,
        }
    matches = matching_exceptions(existing, hosts, sites, bug_id)
    text = (
        f"{len(matches)} matching exception(s):\n{json.dumps(matches, indent=2)}"
        if matches
        else "No matching exceptions."
    )
    return {"content": [{"type": "text", "text": text}]}


def build_exceptions_server() -> McpServerConfig:
    """An in-process MCP server with the `find_etp_exceptions` tool."""
    return create_sdk_mcp_server(
        name=EXCEPTIONS_SERVER_NAME, tools=[find_etp_exceptions]
    )
