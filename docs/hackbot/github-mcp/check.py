"""Check the local GitHub MCP server against a pull request."""

import argparse
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def check(url: str, token: str, owner: str, repo: str, number: int) -> None:
    async with (
        streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (
            read,
            write,
            _,
        ),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = (await session.list_tools()).tools
        names = {tool.name for tool in tools}
        non_read_only = [
            tool.name
            for tool in tools
            if not tool.annotations or not tool.annotations.readOnlyHint
        ]
        if non_read_only:
            raise RuntimeError(f"Tools without read-only annotations: {non_read_only}")
        if "pull_request_read" not in names:
            raise RuntimeError("pull_request_read is missing")

        for method in ("get", "get_files"):
            result = await session.call_tool(
                "pull_request_read",
                {"method": method, "owner": owner, "repo": repo, "pullNumber": number},
            )
            if result.isError:
                raise RuntimeError(f"{method} failed: {result.content}")
            print(f"\n{method}:\n")
            for item in result.content:
                if item.type == "text":
                    print(item.text)

        print(f"\nPASS: read {owner}/{repo}#{number}")
        print(f"PASS: all {len(names)} tools have read-only annotations")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8082/")
    parser.add_argument("--repo", default="Foxbolts/hackbot-github-client-test")
    parser.add_argument("--pr", type=int, default=1)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_MCP_TOKEN")
    if not token:
        parser.error("Set GITHUB_MCP_TOKEN to a GitHub token with read access")
    try:
        owner, repo = args.repo.split("/")
    except ValueError:
        parser.error("--repo must be owner/repository")
    asyncio.run(check(args.url, token, owner, repo, args.pr))


if __name__ == "__main__":
    main()
