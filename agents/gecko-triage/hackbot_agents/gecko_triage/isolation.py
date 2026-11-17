"""Keep the run's secrets away from the CLI that the agent starts.

The CLI and its Bash tool inherit the agent's environment and run as the same user.
`hidden_env` blanks the secrets in the CLI's copy of the environment, and
`make_undumpable` stops processes of the same user from reading the agent's own copy.
"""

import ctypes
import os
import re
import sys
from collections.abc import Mapping

from hackbot_runtime import AgentError

# Runtime variables the CLI never needs. The CLI inherits the agent's environment, and
# its Bash tool can print it, so these are blanked in the CLI's copy.
HIDDEN_ENV = ("RESULTS_POLICY_URL", "RESULTS_POLICY_FIELDS")
_SECRET_NAME = re.compile(
    r"(?:^|_)(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|DSN)(?:_|$)"
    r"|POLICY",
    re.IGNORECASE,
)
# What the CLI reads to reach the model.
_CLI_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_")


def hidden_env(environ: Mapping[str, str]) -> dict[str, str]:
    """A blank value for each secret in ``environ`` the CLI does not need.

    Best effort: it matches names, so a secret under another kind of name passes.
    """
    names = set(HIDDEN_ENV)
    names.update(
        name
        for name in environ
        if _SECRET_NAME.search(name) and not name.startswith(_CLI_ENV_PREFIXES)
    )
    return dict.fromkeys(sorted(names), "")


PR_SET_DUMPABLE = 4


def _libc():
    return ctypes.CDLL(None, use_errno=True)


def make_undumpable() -> None:
    """On Linux, stop other processes of this user from reading this process.

    The blanked variables above are still in this process's own environment. The CLI and
    its Bash run as the same user, so they could read ``/proc/<pid>/environ``. Once the
    process is not dumpable, root owns those files. Elsewhere this does nothing.
    """
    if not sys.platform.startswith("linux"):
        return
    if _libc().prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise AgentError(
            f"prctl(PR_SET_DUMPABLE, 0) failed: {os.strerror(ctypes.get_errno())}"
        )
