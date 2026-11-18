import asyncio
from unittest import mock

import requests
from hackbot_agents.autowebcompat_etp import shims

SHIMS_JS = """
const AVAILABLE_SHIMS = [
  {
    id: "InstagramEmbed",
    name: "Instagram embed placeholder",
    bug: "1892173",
    matches: ["https://www.instagram.com/embed.js"],
    unblocksOnOptIn: ["*://www.instagram.com/*", "*://*.fbcdn.net/*"],
  },
  {
    id: "AdNexusAST",
    name: "AdNexus AST",
    bug: "1734130",
    matches: ["*://*.adnxs.com/*/ast.js*"],
  },
];
"""


def find(args, **get):
    response = mock.Mock(text=SHIMS_JS)
    with mock.patch.object(
        shims.requests, "get", **(get or {"return_value": response})
    ) as fetched:
        reply = asyncio.run(shims.find_shims.handler(args))
    return reply, fetched


def test_find_shims_matches_by_host():
    reply, fetched = find({"hosts": ["https://www.instagram.com/embed.js"]})
    assert not reply.get("is_error")
    text = reply["content"][0]["text"]
    assert text.startswith("1 matching shim(s):")
    assert "InstagramEmbed" in text and "AdNexusAST" not in text
    # Only the patterns that cover the host are listed.
    assert "fbcdn.net" not in text
    assert fetched.call_args.args[0] == shims.SHIMS_URL


def test_find_shims_matches_subdomains_and_wildcards():
    reply, _ = find({"hosts": ["acdn.adnxs.com", "*.fbcdn.net"]})
    text = reply["content"][0]["text"]
    assert text.startswith("2 matching shim(s):")


def test_find_shims_without_a_match():
    reply, _ = find({"hosts": ["example.com"]})
    assert reply["content"][0]["text"] == "No shim covers these hosts."


def test_find_shims_needs_hosts():
    reply, fetched = find({"hosts": []})
    assert reply["is_error"]
    fetched.assert_not_called()


def test_find_shims_reports_a_failed_fetch():
    reply, _ = find({"hosts": ["example.com"]}, side_effect=requests.ConnectionError())
    assert reply["is_error"]
    assert "Could not fetch the shims" in reply["content"][0]["text"]


def test_matching_shims_keeps_id_name_and_bug():
    existing = shims.parse_shims(SHIMS_JS)
    assert shims.matching_shims(existing, ["adnxs.com"]) == [
        {
            "id": "AdNexusAST",
            "name": "AdNexus AST",
            "bug": "1734130",
            "patterns": ["*://*.adnxs.com/*/ast.js*"],
        }
    ]
