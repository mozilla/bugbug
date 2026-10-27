"""Run the blocking Bugzilla calls of the agent-tools handlers off the event loop.

The agent-tools Bugzilla handlers are `async`, but they call Bugzilla with blocking
`requests` calls. The broker serves all its clients on one event loop: the agent and
each subagent that it starts. On that loop, one slow Bugzilla call stops every other
client until it returns. A search that takes 30 seconds then stops all other reads for
30 seconds.

`off_loop` runs each handler in a worker thread, so the calls run at the same time.
`bound_session` gives each request a timeout, so a Bugzilla call that does not end
cannot hold a worker thread forever. The broker uses both for every tool, in both modes.
"""

import asyncio
import concurrent.futures
import functools

import bugsy
from requests.adapters import HTTPAdapter

BUGZILLA_TIMEOUT = 60
MAX_WORKERS = 16
_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=MAX_WORKERS, thread_name_prefix="bugzilla"
)


class TimeoutAdapter(HTTPAdapter):
    """An adapter that sets a timeout on every request that has none.

    Without a timeout, a Bugzilla call that does not end holds a worker thread forever.
    """

    def __init__(self, timeout: float, **kwargs):
        self.timeout = timeout
        super().__init__(**kwargs)

    def send(self, request, timeout=None, **kwargs):
        return super().send(request, timeout=timeout or self.timeout, **kwargs)


def bound_session(client: bugsy.Bugsy) -> None:
    """Give `client` a request timeout and a pool as large as the worker count."""
    adapter = TimeoutAdapter(
        BUGZILLA_TIMEOUT, pool_connections=MAX_WORKERS, pool_maxsize=MAX_WORKERS
    )
    client.session.mount("https://", adapter)
    client.session.mount("http://", adapter)


def off_loop(handler):
    """Wrap ``handler``, so that it runs in a worker thread with its own event loop.

    The agent-tools handlers call Bugzilla with blocking requests. On the server's
    loop, one slow call would stop every other client, also the subagents.
    """

    @functools.wraps(handler)
    async def run(*args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(
            _POOL, lambda: asyncio.run(handler(*args, **kwargs))
        )

    run.off_loop = True
    return run
