"""Lookups in the webcompat addon's SmartBlock shims (`shims.js`).

The agent checks whether a shim already covers a provider with the
`find_shims` tool, so it needs no shell or web access for it.
"""

import json
from collections.abc import Iterable
from typing import Any

import json5
import requests
from claude_agent_sdk import McpServerConfig, create_sdk_mcp_server, tool

from .exceptions import hosts_overlap, pattern_host, to_host

SHIMS_URL = (
    "https://raw.githubusercontent.com/mozilla-firefox/firefox/main/"
    "browser/extensions/webcompat/data/shims.js"
)

SHIMS_SERVER_NAME = "shims"
FIND_SHIMS_TOOL = f"mcp__{SHIMS_SERVER_NAME}__find_shims"


def url_patterns(value: Any) -> list[str]:
    """Every URL pattern in a shim entry, however deeply it is nested.

    Patterns appear as plain strings in `matches` and `unblocksOnOptIn`, in
    `{patterns: [...]}` objects inside them, and in `contentScripts[].matches`.
    """
    if isinstance(value, str):
        return [value] if "://" in value else []
    if isinstance(value, list):
        return [pattern for item in value for pattern in url_patterns(item)]
    if isinstance(value, dict):
        return [pattern for item in value.values() for pattern in url_patterns(item)]
    return []


def parse_shims(source: str) -> list[dict[str, Any]]:
    """Pull each shim's id, name, bug and URL patterns out of `shims.js`.

    The file is JavaScript, but `AVAILABLE_SHIMS` is a plain literal (unquoted
    keys, trailing commas, comments), which JSON5 can parse.
    """
    start = source.index("[", source.index("const AVAILABLE_SHIMS"))
    end = source.index("\n];", start) + len("\n]")
    shims = []
    for entry in json5.loads(source[start:end]):
        shims.append(
            {key: entry[key] for key in ("id", "name", "bug") if key in entry}
            | {"patterns": url_patterns(entry)}
        )
    return shims


def matching_shims(
    existing: list[dict[str, Any]], hosts: Iterable[str]
) -> list[dict[str, Any]]:
    """The shims with a URL pattern covering one of `hosts`, with those patterns."""
    hosts = set(hosts)
    matches = []
    for entry in existing:
        overlapping = [
            pattern
            for pattern in entry["patterns"]
            if any(hosts_overlap(pattern_host(pattern), host) for host in hosts)
        ]
        if overlapping:
            matches.append(
                {key: entry[key] for key in ("id", "name", "bug") if key in entry}
                | {"patterns": overlapping}
            )
    return matches


@tool(
    "find_shims",
    "Look up existing SmartBlock shims of Firefox's webcompat addon "
    "(browser/extensions/webcompat/data/shims.js on main). Returns every shim "
    "with a URL pattern covering one of `hosts`, with its id, name, bug and "
    "the patterns that matched.",
    {
        "type": "object",
        "properties": {
            "hosts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Hosts of the provider's scripts and resources, as host "
                    "names, URLs or match patterns, e.g. `www.instagram.com`."
                ),
            },
        },
        "required": ["hosts"],
        "additionalProperties": False,
    },
)
async def find_shims(args: dict) -> dict:
    hosts = [host for host in (to_host(h) for h in args.get("hosts", [])) if host]
    if not hosts:
        return {
            "content": [{"type": "text", "text": "Pass at least one host."}],
            "is_error": True,
        }
    try:
        response = requests.get(SHIMS_URL, timeout=60)
        response.raise_for_status()
        existing = parse_shims(response.text)
    except (requests.RequestException, ValueError) as exc:
        return {
            "content": [{"type": "text", "text": f"Could not fetch the shims: {exc}"}],
            "is_error": True,
        }
    matches = matching_shims(existing, hosts)
    text = (
        f"{len(matches)} matching shim(s):\n{json.dumps(matches, indent=2)}"
        if matches
        else "No shim covers these hosts."
    )
    return {"content": [{"type": "text", "text": text}]}


def build_shims_server() -> McpServerConfig:
    """An in-process MCP server with the `find_shims` tool."""
    return create_sdk_mcp_server(name=SHIMS_SERVER_NAME, tools=[find_shims])
