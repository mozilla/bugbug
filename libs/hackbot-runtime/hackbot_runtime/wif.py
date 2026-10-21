"""GCP Workload Identity Federation for model-provider and observability auth.

On GCP (Cloud Run, Cloud Functions, GCE, GKE) a workload can fetch a
Google-signed OIDC identity token from the instance metadata server (via the
``google-auth`` library) and exchange it for a short-lived provider access token,
instead of shipping a static API key. Anthropic (Claude SDK/CLI), OpenAI (Codex
CLI) and W&B (wandb/weave SDK) all support this, and each client does the
exchange and refresh itself, driven entirely by environment variables:

  Anthropic
  ``ANTHROPIC_FEDERATION_RULE_ID``             federation rule (``fdrl_…``) ── set by deploy
  ``ANTHROPIC_ORGANIZATION_ID``                Anthropic org id             ── set by deploy
  ``ANTHROPIC_SERVICE_ACCOUNT_ID``             target service account       ── set by deploy
  ``ANTHROPIC_WORKSPACE_ID``                   workspace (optional)         ── set by deploy
  ``ANTHROPIC_IDENTITY_TOKEN_REFRESH_SECONDS`` refresh cadence (optional)   ── set by deploy
  ``ANTHROPIC_IDENTITY_TOKEN_FILE``            path to the Google JWT       ── managed here

  OpenAI
  ``OPENAI_FEDERATION_RULE_ID``                federation rule (``idpm_…``) ── set by deploy
  ``OPENAI_WIF_AUDIENCE``                      audience override (optional) ── set by deploy
  ``OPENAI_IDENTITY_TOKEN_REFRESH_SECONDS``    refresh cadence (optional)   ── set by deploy
  ``OPENAI_IDENTITY_TOKEN_FILE``               path to the Google JWT       ── managed here

  W&B
  ``WANDB_FEDERATION_AUDIENCE``                audience for the Google JWT  ── set by deploy
  ``WANDB_IDENTITY_TOKEN_REFRESH_SECONDS``     refresh cadence (optional)   ── set by deploy
  ``WANDB_IDENTITY_TOKEN_FILE``                path to the Google JWT       ── managed here

The deploy provisions the federation config; this module owns the token file. It
fetches the Google identity token, writes it to a private file, points the
provider's token-file env var at it, and keeps it fresh in a background thread —
Google identity tokens live ~1h, and the clients re-read the file on every
exchange, so a periodically-rewritten file transparently keeps auth alive for
runs longer than a token's lifetime.

When a provider's enabling env var is absent the runtime is in API-key mode and
:func:`configure` is inert for it, so local/compose runs keep using the API key.
"""

import contextlib
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

import google.auth.transport.requests
import google.oauth2.id_token
from google.auth.exceptions import GoogleAuthError

from hackbot_runtime.providers import ProviderError

log = logging.getLogger("hackbot_runtime.wif")

# Google identity tokens expire after ~1h; refresh well inside that window so
# the file always carries plenty of remaining lifetime for the client to exchange.
DEFAULT_REFRESH_INTERVAL = 1800


@dataclass(frozen=True)
class WifConfig:
    """How one provider's client is wired up for federation."""

    # Actual name for logs and errors, e.g. "Anthropic".
    name: str
    # Short identifier for the token directory prefix and refresher thread name.
    provider_id: str
    # Presence of this env var is what flips the runtime from API-key mode to
    # WIF mode — the deploy sets it only on federation-enabled workloads.
    enabled_env: str
    token_file_env: str
    refresh_interval_env: str
    api_key_env: str
    # The audience the metadata token is minted for; must match the provider's
    # federation rule. Read from ``audience_env`` when set, else ``default_audience``.
    default_audience: str | None = None
    audience_env: str | None = None
    # Whether a static API key takes precedence over federation in the client.
    # If so, a leftover key would silently shadow WIF, so configure() refuses.
    api_key_shadows_wif: bool = True

    def is_enabled(self) -> bool:
        """True when the deploy provisioned federation for this provider."""
        return bool(os.environ.get(self.enabled_env))

    def audience(self) -> str:
        audience = (
            self.audience_env and os.environ.get(self.audience_env)
        ) or self.default_audience
        if not audience:
            raise ProviderError(f"No {self.name} federation audience is configured.")
        return audience


# See https://platform.claude.com/docs/en/manage-claude/wif-providers/gcp
ANTHROPIC = WifConfig(
    name="Anthropic",
    provider_id="anthropic",
    enabled_env="ANTHROPIC_FEDERATION_RULE_ID",
    token_file_env="ANTHROPIC_IDENTITY_TOKEN_FILE",
    refresh_interval_env="ANTHROPIC_IDENTITY_TOKEN_REFRESH_SECONDS",
    api_key_env="ANTHROPIC_API_KEY",
    default_audience="https://api.anthropic.com",
)

# See https://developers.openai.com/api/docs/guides/workload-identity-federation
OPENAI = WifConfig(
    name="OpenAI",
    provider_id="openai",
    enabled_env="OPENAI_FEDERATION_RULE_ID",
    token_file_env="OPENAI_IDENTITY_TOKEN_FILE",
    refresh_interval_env="OPENAI_IDENTITY_TOKEN_REFRESH_SECONDS",
    api_key_env="OPENAI_API_KEY",
    default_audience="https://api.openai.com/v1",
    audience_env="OPENAI_WIF_AUDIENCE",
    api_key_shadows_wif=False,
)

# See https://docs.wandb.ai/platform/hosting/iam/identity_federation
WANDB = WifConfig(
    name="W&B",
    provider_id="wandb",
    enabled_env="WANDB_FEDERATION_AUDIENCE",
    token_file_env="WANDB_IDENTITY_TOKEN_FILE",
    refresh_interval_env="WANDB_IDENTITY_TOKEN_REFRESH_SECONDS",
    api_key_env="WANDB_API_KEY",
    audience_env="WANDB_FEDERATION_AUDIENCE",
)

PROVIDERS = (ANTHROPIC, OPENAI, WANDB)

# Populated once configure() succeeds for a provider (keyed by provider_id);
# keeps the daemon refresher alive for the process lifetime and makes a second
# configure() call a no-op.
_refreshers: dict[str, TokenFileRefresher] = {}


def fetch_gcp_identity_token(audience: str) -> str:
    """Fetch a Google-signed OIDC identity token for ``audience``.

    Delegates to ``google.oauth2.id_token``, which sources the token from the
    GCE/Cloud Run/GKE metadata server (or a ``GOOGLE_APPLICATION_CREDENTIALS``
    service-account file for local testing). On the metadata path it requests
    ``format=full``, so the token carries the ``email`` claim federation rules
    match on — without it the Anthropic exchange fails with ``invalid_grant``.
    """
    request = google.auth.transport.requests.Request()
    try:
        token = google.oauth2.id_token.fetch_id_token(request, audience)
    except GoogleAuthError as exc:
        # Surface provider-credential failures as a single ProviderError type so
        # callers (and the refresh loop) handle them uniformly.
        raise ProviderError(
            f"Failed to fetch a Google identity token for audience {audience}: {exc}"
        ) from exc
    if not token:
        raise ProviderError(
            "Google returned an empty identity token; the workload has no usable "
            "service account."
        )
    return token


def refresh_interval(env_var: str) -> float:
    raw = os.environ.get(env_var)
    if not raw:
        return DEFAULT_REFRESH_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        log.warning(
            "%s=%r is not a number; falling back to %ss",
            env_var,
            raw,
            DEFAULT_REFRESH_INTERVAL,
        )
        return DEFAULT_REFRESH_INTERVAL
    if value <= 0:
        log.warning(
            "%s=%s is not positive; falling back to %ss",
            env_var,
            raw,
            DEFAULT_REFRESH_INTERVAL,
        )
        return DEFAULT_REFRESH_INTERVAL
    return value


def _default_token_path(provider_id: str) -> Path:
    """A private, process-owned, absolute path for the identity token file.

    ``mkdtemp`` gives a 0700 directory so the bearer token isn't world-readable.
    The path is absolute, as Codex requires.
    """
    return Path(tempfile.mkdtemp(prefix=f"{provider_id}-wif-")) / "identity-token"


class TokenFileRefresher:
    """Keeps ``token_file`` populated with a fresh Google identity token.

    The first write happens synchronously in :meth:`start` so a metadata-server
    failure surfaces immediately (fail fast) rather than as an opaque auth error
    once the agent is mid-run. Subsequent writes run on a daemon thread.
    """

    def __init__(
        self,
        token_file: Path,
        interval: float,
        audience: str,
        provider_id: str,
        name: str,
    ):
        self._token_file = token_file
        self._interval = interval
        self._audience = audience
        self._name = name
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"{provider_id}-wif-refresh", daemon=True
        )

    def _write_token(self) -> None:
        """Atomically replace the token file so clients never read a partial write."""
        token = fetch_gcp_identity_token(self._audience)
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._token_file.parent, prefix=".identity-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token)
            os.replace(tmp, self._token_file)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _loop(self) -> None:
        # wait() returns True only when stop() is set, so the loop exits cleanly
        # on shutdown and otherwise re-writes the token every interval.
        while not self._stop.wait(self._interval):
            try:
                self._write_token()
            except (ProviderError, OSError) as exc:
                # A transient metadata blip is survivable: the previous token is
                # still on disk and valid for a while, so log and keep the loop
                # alive to retry on the next tick rather than crashing the thread.
                log.warning(
                    "Failed to refresh %s WIF identity token; "
                    "serving the previously written token: %s",
                    self._name,
                    exc,
                )

    def start(self) -> None:
        self._write_token()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def configure(config: WifConfig) -> bool:
    """Wire up WIF auth for ``config``'s provider client when the deploy enables it.

    Returns ``True`` when WIF was configured, ``False`` in API-key mode. Idempotent:
    a second call while the provider's refresher is already running is a no-op.
    """
    if not config.is_enabled():
        return False
    if config.provider_id in _refreshers:
        return True

    if os.environ.get(config.api_key_env):
        if config.api_key_shadows_wif:
            log.error(
                "%s is set while %s Workload Identity Federation is configured; "
                "the API key takes precedence and shadows WIF. Unset it if you "
                "intend to authenticate via federation.",
                config.api_key_env,
                config.name,
            )
            return False
        log.warning(
            "%s is set while %s Workload Identity Federation is configured; "
            "federation will be used instead. Unset %s to keep a long-lived key "
            "out of the container.",
            config.api_key_env,
            config.name,
            config.api_key_env,
        )

    token_file = Path(
        os.environ.get(config.token_file_env) or _default_token_path(config.provider_id)
    )
    interval = refresh_interval(config.refresh_interval_env)
    audience = config.audience()
    refresher = TokenFileRefresher(
        token_file, interval, audience, config.provider_id, config.name
    )
    refresher.start()
    os.environ[config.token_file_env] = str(token_file)
    _refreshers[config.provider_id] = refresher
    log.info(
        "%s auth: GCP Workload Identity Federation "
        "(audience %s, identity token file %s, refresh every %ss)",
        config.name,
        audience,
        token_file,
        interval,
    )
    return True
