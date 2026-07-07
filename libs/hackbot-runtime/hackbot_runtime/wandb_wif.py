"""GCP Workload Identity Federation for Weights & Biases (Weave) auth.

W&B identity federation lets the wandb/weave SDK authenticate from a short-lived
JWT instead of a long-lived ``WANDB_API_KEY``: the SDK reads a JWT from the file
named by ``WANDB_IDENTITY_TOKEN_FILE`` and exchanges it for a W&B access token. On
GCP we mint that JWT as a Google-signed OIDC identity token (from the metadata
server, via ``google-auth``), so the agent container holds no W&B credential at
all -- the same shape as :mod:`hackbot_runtime.anthropic_wif`.

  ``WANDB_FEDERATION_AUDIENCE``          audience for the Google JWT  ── set by deploy
  ``WANDB_IDENTITY_TOKEN_FILE``          path to the Google JWT       ── managed here
  ``WANDB_IDENTITY_TOKEN_REFRESH_SECONDS`` refresh cadence (optional) ── set by deploy

The deploy provisions the audience (which must match the W&B org's federation
trust config); this module owns the token file. It fetches the identity token,
writes it to a private file, points ``WANDB_IDENTITY_TOKEN_FILE`` at it, and keeps
it fresh in a background thread — Google identity tokens live ~1h and the SDK
re-reads the file when it re-exchanges, so a periodically-rewritten file keeps
auth alive for runs longer than a token's lifetime.

When ``WANDB_FEDERATION_AUDIENCE`` is absent the runtime is in API-key mode and
this module is inert, so local/compose runs keep using ``WANDB_API_KEY``.

See https://docs.wandb.ai/platform/hosting/iam/identity_federation
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from pathlib import Path

import google.auth.transport.requests
import google.oauth2.id_token
from google.auth.exceptions import GoogleAuthError

from hackbot_runtime.providers import ProviderError

log = logging.getLogger("hackbot_runtime.wandb_wif")

# Presence of the audience is what flips the runtime from API-key mode to WIF
# mode — the deploy sets it only on federation-enabled workloads. Its value is
# the audience the Google identity token is minted for, which must match the
# W&B org's federation trust configuration.
AUDIENCE_ENV = "WANDB_FEDERATION_AUDIENCE"
TOKEN_FILE_ENV = "WANDB_IDENTITY_TOKEN_FILE"
REFRESH_INTERVAL_ENV = "WANDB_IDENTITY_TOKEN_REFRESH_SECONDS"

# Google identity tokens expire after ~1h; refresh well inside that window so
# the file always carries plenty of remaining lifetime for the SDK to exchange.
DEFAULT_REFRESH_INTERVAL = 1800

# Set once configure() succeeds; keeps the daemon refresher alive for the
# process lifetime and makes a second configure() call a no-op.
_refresher: _TokenFileRefresher | None = None


def is_enabled() -> bool:
    """True when the deploy provisioned a federation audience (WIF mode)."""
    return bool(os.environ.get(AUDIENCE_ENV))


def fetch_gcp_identity_token(audience: str) -> str:
    """Fetch a Google-signed OIDC identity token for ``audience``.

    Delegates to ``google.oauth2.id_token``, which sources the token from the
    GCE/Cloud Run/GKE metadata server (or a ``GOOGLE_APPLICATION_CREDENTIALS``
    service-account file for local testing).
    """
    request = google.auth.transport.requests.Request()
    try:
        token = google.oauth2.id_token.fetch_id_token(request, audience)
    except GoogleAuthError as exc:
        raise ProviderError(
            f"Failed to fetch a Google identity token for W&B federation: {exc}"
        ) from exc
    if not token:
        raise ProviderError(
            "Google returned an empty identity token; the workload has no usable "
            "service account."
        )
    return token


def _refresh_interval() -> float:
    raw = os.environ.get(REFRESH_INTERVAL_ENV)
    if not raw:
        return DEFAULT_REFRESH_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        log.warning(
            "%s=%r is not a number; falling back to %ss",
            REFRESH_INTERVAL_ENV,
            raw,
            DEFAULT_REFRESH_INTERVAL,
        )
        return DEFAULT_REFRESH_INTERVAL
    if value <= 0:
        log.warning(
            "%s=%s is not positive; falling back to %ss",
            REFRESH_INTERVAL_ENV,
            raw,
            DEFAULT_REFRESH_INTERVAL,
        )
        return DEFAULT_REFRESH_INTERVAL
    return value


def _default_token_path() -> Path:
    """A private, process-owned path for the identity token file.

    ``mkdtemp`` gives a 0700 directory so the bearer token isn't world-readable.
    """
    return Path(tempfile.mkdtemp(prefix="wandb-wif-")) / "identity-token"


class _TokenFileRefresher:
    """Keeps ``token_file`` populated with a fresh Google identity token.

    The first write happens synchronously in :meth:`start` so a metadata-server
    failure surfaces immediately (fail fast) rather than as an opaque auth error
    once the agent is mid-run. Subsequent writes run on a daemon thread.
    """

    def __init__(self, token_file: Path, interval: float, audience: str):
        self._token_file = token_file
        self._interval = interval
        self._audience = audience
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="wandb-wif-refresh", daemon=True
        )

    def _write_token(self) -> None:
        """Atomically replace the token file so the SDK never reads a partial write."""
        token = fetch_gcp_identity_token(self._audience)
        self._token_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._token_file.parent, prefix=".identity-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(token)
            os.replace(tmp, self._token_file)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._write_token()
            except (ProviderError, OSError) as exc:
                log.warning(
                    "Failed to refresh W&B WIF identity token; "
                    "serving the previously written token: %s",
                    exc,
                )

    def start(self) -> None:
        self._write_token()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def configure() -> bool:
    """Wire up W&B WIF auth for the weave SDK when the deploy enables it.

    Returns ``True`` when WIF was configured, ``False`` in API-key mode. Idempotent:
    a second call while the refresher is already running is a no-op.
    """
    global _refresher
    if not is_enabled():
        return False
    if _refresher is not None:
        return True

    if os.environ.get("WANDB_API_KEY"):
        log.error(
            "WANDB_API_KEY is set while Workload Identity Federation is configured; "
            "the API key takes precedence and shadows WIF. Unset it if you intend "
            "to authenticate via federation."
        )
        return False

    audience = os.environ[AUDIENCE_ENV]
    token_file = Path(os.environ.get(TOKEN_FILE_ENV) or _default_token_path())
    interval = _refresh_interval()
    refresher = _TokenFileRefresher(token_file, interval, audience)
    refresher.start()
    os.environ[TOKEN_FILE_ENV] = str(token_file)
    _refresher = refresher
    log.info(
        "W&B auth: GCP Workload Identity Federation "
        "(identity token file %s, refresh every %ss)",
        token_file,
        interval,
    )
    return True
