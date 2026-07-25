"""Thin client for triggering runs over the public hackbot API.

The webhook receiver uses this instead of calling the run-creation internals
directly, so it stays decoupled from the DB/jobs layer and can be lifted into a
standalone ``hackbot-webhook`` service later by just repointing ``base_url``.
While co-located, this is a loopback call to the same service. Config is injected
(base URL + API key) rather than read from settings, so it's easy to construct in
a dependency and swap in tests. Mirrors ``hackbot-pulse-listener/app/client.py``
(async here).
"""

import httpx

_TIMEOUT = httpx.Timeout(30.0)


class HackbotClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url
        self._api_key = api_key

    async def trigger_run(self, agent_name: str, inputs: dict) -> str:
        """Create a run via ``POST /agents/{agent_name}/runs``; return its run id."""
        url = f"{self._base_url}/agents/{agent_name}/runs"
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url, json=inputs, headers={"X-API-Key": self._api_key}
            )
            resp.raise_for_status()
            return resp.json()["run_id"]
