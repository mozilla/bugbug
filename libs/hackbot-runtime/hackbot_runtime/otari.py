"""Route an agent's model traffic through otari.ai.

otari.ai (https://otari.ai) is an LLM gateway that records cost, enforces
budgets and can serve a run from another provider. With ``OTARI_ENABLED=1``,
both the Claude SDK/CLI and Codex are pointed at it and every request is tagged
with the run and the agent; otherwise this module does nothing.

Authentication is unchanged: otari.ai answers the same Workload Identity
Federation exchange Anthropic does, so the deploy only points the Anthropic
federation ids at the otari.ai rule and organization. The identity token is
minted for otari.ai's own audience, which is the only kind it accepts.

Codex does its own OpenAI federation only against api.openai.com, so it gets an
``otari`` model provider instead, whose bearer token comes from running this
module: it makes the exchange Claude Code makes, with the same env. See
docs/hackbot/runtime.md.
"""

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

import httpx

log = logging.getLogger("hackbot_runtime.otari")

ENABLED_ENV = "OTARI_ENABLED"
BASE_URL_ENV = "OTARI_BASE_URL"

# The SDK appends /v1/messages, Codex /responses to its base, and the
# federation exchange /v1/oauth/token.
DEFAULT_BASE_URL = "https://api.otari.ai/api"

# otari.ai refuses identity tokens minted for any audience but its own.
AUDIENCE = "https://api.otari.ai"

# Claude Code reads extra request headers from this variable, one
# ``Name: value`` per line.
CUSTOM_HEADERS_ENV = "ANTHROPIC_CUSTOM_HEADERS"

# otari.ai access tokens last 10 minutes by default; Codex reruns the token
# command this often.
TOKEN_REFRESH_MS = 5 * 60 * 1000


def configure(run_id: str, agent: str) -> None:
    """Point the Claude SDK/CLI and Codex at otari.ai when the deploy enables it."""
    if os.environ.get(ENABLED_ENV) != "1":
        return

    base_url = os.environ.get(BASE_URL_ENV) or DEFAULT_BASE_URL
    headers = {
        # One session per run, across every query(), thread and subagent it makes.
        "Otari-Conversation-Id": f"hackbot-{run_id}",
        "Otari-Tags": f"app=hackbot,agent={agent}",
        # otari.ai answers web search for a model with no search of its own.
        "Otari-Web-Search": "auto",
    }

    os.environ["ANTHROPIC_BASE_URL"] = base_url
    os.environ.setdefault("ANTHROPIC_AUDIENCE", AUDIENCE)
    lines = [os.environ.get(CUSTOM_HEADERS_ENV, "").strip()]
    lines += [f"{name}: {value}" for name, value in headers.items()]
    os.environ[CUSTOM_HEADERS_ENV] = "\n".join(line for line in lines if line)

    # Left set, Codex would exchange the identity token at OpenAI, not here.
    if os.environ.pop("OPENAI_FEDERATION_RULE_ID", None):
        log.info("OPENAI_FEDERATION_RULE_ID ignored: Codex authenticates at otari.ai")
    codex_home = Path(tempfile.mkdtemp(prefix="codex-otari-"))
    (codex_home / "config.toml").write_text(_codex_config(base_url, headers))
    os.environ["CODEX_HOME"] = str(codex_home)

    log.info("Model traffic routed through otari.ai at %s", base_url)


def _codex_config(base_url: str, headers: dict[str, str]) -> str:
    # JSON strings are valid TOML basic strings.
    http_headers = ", ".join(
        f"{json.dumps(k)} = {json.dumps(v)}" for k, v in headers.items()
    )
    return f"""\
model_provider = "otari"

[model_providers.otari]
name = "otari.ai"
base_url = {json.dumps(f"{base_url}/v1")}
wire_api = "responses"
http_headers = {{ {http_headers} }}

[model_providers.otari.auth]
command = {json.dumps(sys.executable)}
args = ["-m", "hackbot_runtime.otari"]
refresh_interval_ms = {TOKEN_REFRESH_MS}
"""


def access_token() -> str:
    """The bearer token Claude Code would present to otari.ai, from the same env.

    Deployed, the identity token the runtime keeps fresh is exchanged for a
    short-lived access token; locally, ``ANTHROPIC_API_KEY`` is an otari.ai key.
    """
    rule_id = os.environ.get("ANTHROPIC_FEDERATION_RULE_ID")
    if not rule_id:
        if key := os.environ.get("ANTHROPIC_API_KEY"):
            return key
        raise RuntimeError(
            "Set ANTHROPIC_FEDERATION_RULE_ID, or ANTHROPIC_API_KEY to an otari.ai key"
        )

    assertion = Path(os.environ["ANTHROPIC_IDENTITY_TOKEN_FILE"]).read_text().strip()
    grant = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
        "federation_rule_id": rule_id,
    }
    if organization_id := os.environ.get("ANTHROPIC_ORGANIZATION_ID"):
        grant["organization_id"] = organization_id
    response = httpx.post(
        f"{os.environ['ANTHROPIC_BASE_URL']}/v1/oauth/token", json=grant, timeout=30
    )
    if response.is_error:
        raise RuntimeError(
            f"otari.ai refused the token exchange ({response.status_code}): "
            f"{response.text}"
        )
    return response.json()["access_token"]


if __name__ == "__main__":
    # Codex's token command: the token on stdout, any failure on stderr.
    try:
        print(access_token())
    except Exception as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
