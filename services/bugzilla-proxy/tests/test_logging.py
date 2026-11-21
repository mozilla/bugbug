"""The audit trail has to survive the way the container starts the app."""

import os
import subprocess
import sys

# Imports the app the way `uvicorn bugzilla_proxy.main:app` does, in a fresh
# interpreter so nothing pytest configured can mask a missing handler, then
# emits an audit line through the app's own logger.
_PROBE = """
import logging
import bugzilla_proxy.main  # noqa: F401
logging.getLogger("bugzilla_proxy.app").info("audit probe")
"""


def test_audit_lines_are_emitted_when_served_by_uvicorn(keypair):
    _private, public_pem = keypair
    env = {**os.environ, "JWT_PUBLIC_KEY": public_pem, "TOKEN_ISSUER": ""}
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert "audit probe" in result.stderr
