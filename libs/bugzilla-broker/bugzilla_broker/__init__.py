"""A sidecar that holds the Bugzilla API key and serves the Bugzilla read tools over MCP.

An agent reaches it at `<BROKER_URL>/mcp`, and its container needs no Bugzilla key. See
`bugzilla_broker.app` for the modes and the settings.
"""

from bugzilla_broker.app import BrokerSettings, create_app, mcp_manager
from bugzilla_broker.public import public_tools
from bugzilla_broker.visibility import marked_public

__all__ = [
    "BrokerSettings",
    "create_app",
    "marked_public",
    "mcp_manager",
    "public_tools",
]
