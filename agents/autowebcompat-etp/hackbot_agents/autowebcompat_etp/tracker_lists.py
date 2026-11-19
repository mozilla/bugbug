"""Lookups in the tracker lists ETP blocks with (mozilla-services/shavar-prod-lists).

Firefox's lists are built from this repository, but a profile only picks up a
change once it downloads the new list version. A site that a recent removal
fixed can therefore still be broken for a reporter whose profile has older
lists, while a fresh profile works: the `check_tracker_lists` tool shows the
agent such changes.
"""

import functools
import json
import logging
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from claude_agent_sdk import McpServerConfig, create_sdk_mcp_server, tool

from .exceptions import hosts_overlap, to_host

logger = logging.getLogger("autowebcompat-etp")

REPO_URL = "https://github.com/mozilla-services/shavar-prod-lists.git"
WEB_URL = "https://github.com/mozilla-services/shavar-prod-lists"
BLOCKLISTS = ("disconnect-blacklist.json", "social-tracking-protection-blacklist.json")
ENTITY_LIST = "disconnect-entitylist.json"
HISTORY_DAYS = 180

TRACKER_LISTS_SERVER_NAME = "tracker-lists"
CHECK_TRACKER_LISTS_TOOL = f"mcp__{TRACKER_LISTS_SERVER_NAME}__check_tracker_lists"

_clone: Path | None = None


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    ).stdout


def ensure_clone() -> Path:
    """A clone of the default branch, made once per process.

    The full history is only a few MB, and history searches (`git log -S`) need
    every file version, which a blob-less clone would fetch one at a time.
    """
    global _clone
    if _clone is None:
        path = Path(tempfile.mkdtemp(prefix="shavar-prod-lists-")) / "repo"
        subprocess.run(
            ["git", "clone", "--quiet", REPO_URL, str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        _clone = path
    return _clone


def commit_links(sha: str, subject: str) -> dict[str, str]:
    """GitHub links for a commit, plus its pull request if the subject names one."""
    links = {"url": f"{WEB_URL}/commit/{sha}"}
    if match := re.search(r"\(#(\d+)\)\s*$", subject):
        links["pull_request"] = f"{WEB_URL}/pull/{match.group(1)}"
    return links


def occurrences(repo: Path, revision: str, file: str, needle: str) -> int:
    """How often `needle` occurs in `file` at `revision` (0 if it doesn't exist)."""
    try:
        return git(repo, "show", f"{revision}:{file}").count(needle)
    except subprocess.CalledProcessError:
        return 0


@functools.cache
def added_by(repo: Path, file: str, domain: str) -> dict[str, str] | None:
    """The latest commit that added `domain` to `file`, with links to it.

    `git log -S` lists the commits changing how often the quoted domain occurs
    in the file; the latest one raising that count added the current entry.
    """
    needle = json.dumps(domain)
    log = git(repo, "log", "-S", needle, "--format=%h%x09%cs%x09%s", "--", file)
    for line in log.splitlines():
        sha, date, subject = line.split("\t", 2)
        if occurrences(repo, sha, file, needle) > occurrences(
            repo, f"{sha}^", file, needle
        ):
            return {
                "commit": sha,
                "date": date,
                "subject": subject,
                **commit_links(sha, subject),
            }
    return None


def blocklist_entries(lists: dict[str, Any], host: str) -> list[dict[str, str]]:
    """Every blocklist domain overlapping `host`, with its list, category and org."""
    entries = []
    for list_name, data in lists.items():
        for category, orgs in data["categories"].items():
            for org_entry in orgs:
                for org, sites in org_entry.items():
                    for domains in sites.values():
                        if not isinstance(domains, list):
                            continue
                        for domain in domains:
                            if isinstance(domain, str) and hosts_overlap(domain, host):
                                entries.append(
                                    {
                                        "list": list_name,
                                        "category": category,
                                        "org": org,
                                        "domain": domain,
                                    }
                                )
    return entries


def entity_entries(entities: dict[str, Any], host: str) -> list[dict[str, Any]]:
    """Entities owning `host`. Their properties may load their resources as first party.

    `matched` lists the entity's domains overlapping `host`.
    """
    matches = []
    for org, entity in entities["entities"].items():
        owned = entity.get("properties", []) + entity.get("resources", [])
        matched = [domain for domain in owned if hosts_overlap(domain, host)]
        if matched:
            matches.append(
                {
                    "org": org,
                    "properties": entity.get("properties", []),
                    "resources": entity.get("resources", []),
                    "matched": list(dict.fromkeys(matched)),
                }
            )
    return matches


def with_added_by(
    repo: Path, host: str, lists: dict[str, Any], entities: dict[str, Any]
) -> dict[str, Any]:
    """The blocklist entries and entities for `host`, each with the commit adding it."""
    blocked = [
        {**entry, "added_by": added_by(repo, entry["list"], entry["domain"])}
        for entry in blocklist_entries(lists, host)
    ]
    owners = [
        {
            **entity,
            "added_by": {
                domain: added_by(repo, ENTITY_LIST, domain)
                for domain in entity["matched"]
            },
        }
        for entity in entity_entries(entities, host)
    ]
    return {"blocklist_entries": blocked, "entities": owners}


_changes: list[dict[str, Any]] | None = None


def list_changes(repo: Path, days: int = HISTORY_DAYS) -> list[dict[str, Any]]:
    """Every list line added or removed in the last `days`, newest first.

    Read once from a single `git log -p` and cached, so looking up many hosts
    (all third-party hosts of a page) costs no more than looking up one.
    """
    global _changes
    if _changes is not None:
        return _changes
    files = [*BLOCKLISTS, ENTITY_LIST]
    log = git(
        repo,
        "log",
        f"--since={days} days ago",
        "-p",
        "--unified=0",
        "--format=%x00%h%x09%cs%x09%s",
        "--",
        *files,
    )
    changes: list[dict[str, Any]] = []
    for entry in log.split("\0")[1:]:
        header, _, diff = entry.partition("\n")
        sha, date, subject = header.split("\t", 2)
        file = None
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                file = line.removeprefix("+++ b/")
            elif line[:1] in ("+", "-") and not line.startswith(("+++", "---")):
                value = line[1:].strip().strip(",").strip('"')
                if value and file:
                    changes.append(
                        {
                            "commit": sha,
                            "date": date,
                            "subject": subject,
                            "file": file,
                            "change": "added" if line[0] == "+" else "removed",
                            "value": value,
                        }
                    )
    _changes = changes
    return changes


def history(repo: Path, host: str, days: int = HISTORY_DAYS) -> list[dict[str, Any]]:
    """Commits in the last `days` that added or removed list lines naming `host`."""
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for change in list_changes(repo, days):
        if host not in change["value"]:
            continue
        key = (change["commit"], change["file"])
        if key not in grouped:
            grouped[key] = {
                "commit": change["commit"],
                "date": change["date"],
                "subject": change["subject"],
                "file": change["file"],
                **commit_links(change["commit"], change["subject"]),
                "added": [],
                "removed": [],
            }
        grouped[key][change["change"]].append(change["value"])
    return list(grouped.values())


def check_hosts(hosts: list[str]) -> dict[str, Any]:
    repo = ensure_clone()
    lists = {name: json.loads((repo / name).read_text()) for name in BLOCKLISTS}
    entities = json.loads((repo / ENTITY_LIST).read_text())
    head = git(repo, "log", "-1", "--format=%h %cs").strip()
    return {
        "lists_commit": head,
        "hosts": {
            host: {
                **with_added_by(repo, host, lists, entities),
                "recent_changes": history(repo, host),
            }
            for host in hosts
        },
    }


@tool(
    "check_tracker_lists",
    "Look up hosts in the tracker lists ETP blocks with (the default branch of "
    "mozilla-services/shavar-prod-lists, which current lists are built from). For "
    "each host: the blocklist entries covering it today (list, category, org) and "
    "the entities owning it, each with `added_by`, the commit that added it; and "
    "the commits of the last 180 days that added or removed lines naming it. "
    "Commits come with a GitHub `url` and, if merged from one, a `pull_request` "
    "link. Pass a base domain (e.g. `editmysite.com`) to cover "
    "all its subdomains in the history search.",
    {
        "type": "object",
        "properties": {
            "hosts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Hosts, URLs or match patterns to look up.",
            },
        },
        "required": ["hosts"],
        "additionalProperties": False,
    },
)
async def check_tracker_lists(args: dict) -> dict:
    hosts = [to_host(h) for h in args.get("hosts", []) if h]
    if not hosts:
        return {
            "content": [{"type": "text", "text": "Pass at least one host."}],
            "is_error": True,
        }
    try:
        result = check_hosts(hosts)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError) as exc:
        logger.exception("could not check the tracker lists")
        return {
            "content": [
                {"type": "text", "text": f"Could not check the tracker lists: {exc}"}
            ],
            "is_error": True,
        }
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}


def build_tracker_lists_server() -> McpServerConfig:
    """An in-process MCP server with the `check_tracker_lists` tool."""
    return create_sdk_mcp_server(
        name=TRACKER_LISTS_SERVER_NAME, tools=[check_tracker_lists]
    )
