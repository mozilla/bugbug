"""GCP Workload Identity Federation for OpenAI API auth.

On GCP (Cloud Run, Cloud Functions, GCE, GKE) a workload can fetch a
Google-signed OIDC identity token from the instance metadata server (via the
``google-auth`` library) and exchange it for a short-lived OpenAI access
token, instead of shipping a static ``OPENAI_API_KEY``. The Codex CLI does the
exchange and refresh itself, driven entirely by environment variables:

  ``OPENAI_FEDERATION_RULE_ID``             federation rule (``idpm_…``)  ── set by deploy
  ``OPENAI_WIF_AUDIENCE``                   audience override (optional)  ── set by deploy
  ``OPENAI_IDENTITY_TOKEN_REFRESH_SECONDS`` refresh cadence (optional)    ── set by deploy
  ``OPENAI_IDENTITY_TOKEN_FILE``            path to the Google JWT        ── managed here

The deploy provisions the federation rule id; this module owns the token file. It
fetches the Google identity token, writes it to a private file, points
``OPENAI_IDENTITY_TOKEN_FILE`` at it, and keeps it fresh in a background thread —
Google identity tokens live ~1h, and Codex re-reads the file whenever it needs
another OpenAI access token, so a periodically-rewritten file transparently keeps
auth alive for runs longer than a token's lifetime.

When ``OPENAI_FEDERATION_RULE_ID`` is absent the runtime is in API-key mode and
this module is inert, so local/compose runs keep using ``OPENAI_API_KEY``.

See https://developers.openai.com/api/docs/guides/workload-identity-federation
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import threading
from pathlib import Path

import google.auth.transport.requests
import google.oauth2.id_token
from google.auth.exceptions import GoogleAuthError

from hackbot_runtime.config import OpenaiAuthSettings, OpenaiWifSettings
from hackbot_runtime.providers import ProviderError

log = logging.getLogger("hackbot_runtime.openai_wif")

# Default audience the metadata token is minted for; must match the federation
# rule's audience on the OpenAI side. Overridable via ``AUDIENCE_ENV``.
OPENAI_AUDIENCE = "https://api.openai.com/v1"

# Presence of the federation rule id is what flips the runtime from API-key mode
# to WIF mode — the deploy sets it only on federation-enabled workloads.
RULE_ID_ENV = "OPENAI_FEDERATION_RULE_ID"
AUDIENCE_ENV = "OPENAI_WIF_AUDIENCE"
TOKEN_FILE_ENV = "OPENAI_IDENTITY_TOKEN_FILE"
REFRESH_INTERVAL_ENV = "OPENAI_IDENTITY_TOKEN_REFRESH_SECONDS"

# Google identity tokens expire after ~1h; refresh well inside that window so
# the file always carries plenty of remaining lifetime for Codex to exchange.
DEFAULT_REFRESH_INTERVAL = 1800

# Set once configure() succeeds; keeps the daemon refresher alive for the
# process lifetime and makes a second configure() call a no-op.
_refresher: _TokenFileRefresher | None = None


def is_enabled() -> bool:
    """True when the deploy provisioned a federation rule (WIF mode)."""
    return bool(OpenaiWifSettings().rule_id)


def _audience() -> str:
    return OpenaiWifSettings().audience


def fetch_gcp_identity_token(audience: str = OPENAI_AUDIENCE) -> str:
    """Fetch a Google-signed OIDC identity token for ``audience``.

    Delegates to ``google.oauth2.id_token``, which sources the token from the
    GCE/Cloud Run/GKE metadata server (or a ``GOOGLE_APPLICATION_CREDENTIALS``
    service-account file for local testing). On the metadata path it requests
    ``format=full``, so the token carries the ``email`` claim a federation rule
    can match on.
    """
    request = google.auth.transport.requests.Request()
    try:
        token = google.oauth2.id_token.fetch_id_token(request, audience)
    except GoogleAuthError as exc:
        # Surface provider-credential failures as a single ProviderError type so
        # callers (and the refresh loop) handle them uniformly.
        raise ProviderError(
            f"Failed to fetch a Google identity token for OpenAI federation: {exc}"
        ) from exc
    if not token:
        raise ProviderError(
            "Google returned an empty identity token; the workload has no usable "
            "service account."
        )
    return token


def _refresh_interval() -> float:
    settings = OpenaiWifSettings()
    raw = settings.refresh_interval
    if not raw:
        return DEFAULT_REFRESH_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        log.warning(
            "%s=%r is not a number; falling back to %ss",
            OpenaiWifSettings.REFRESH_INTERVAL_ENV,
            raw,
            OpenaiWifSettings.DEFAULT_REFRESH_INTERVAL,
        )
        return OpenaiWifSettings.DEFAULT_REFRESH_INTERVAL
    if value <= 0:
        log.warning(
            "%s=%s is not positive; falling back to %ss",
            OpenaiWifSettings.REFRESH_INTERVAL_ENV,
            raw,
            OpenaiWifSettings.DEFAULT_REFRESH_INTERVAL,
        )
        return OpenaiWifSettings.DEFAULT_REFRESH_INTERVAL
    return value


class _TokenFileRefresher:
    """Keeps ``token_file`` populated with a fresh Google identity token.

    The first write happens synchronously in :meth:`start` so a metadata-server
    failure surfaces immediately (fail fast) rather than as an opaque auth error
    once the agent is mid-run. Subsequent writes run on a daemon thread.
    """

    def __init__(
        self, token_file: Path, interval: float, audience: str = OPENAI_AUDIENCE
    ):
        self._token_file = token_file
        self._interval = interval
        self._audience = audience
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="openai-wif-refresh", daemon=True
        )

    def _write_token(self) -> None:
        """Atomically replace the token file so Codex never reads a partial write."""
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
                    "Failed to refresh OpenAI WIF identity token; "
                    "serving the previously written token: %s",
                    exc,
                )

    def start(self) -> None:
        self._write_token()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def configure() -> bool:
    """Wire up OpenAI WIF auth for Codex when the deploy enables it.

    Returns ``True`` when WIF was configured, ``False`` in API-key mode. Idempotent:
    a second call while the refresher is already running is a no-op.
    """
    global _refresher
    if not is_enabled():
        return False
    if _refresher is not None:
        return True

    auth_settings = OpenaiAuthSettings()
    if auth_settings.api_key:
        log.warning(
            "OPENAI_API_KEY is set while Workload Identity Federation is "
            "configured; Codex will use federation instead. Unset "
            "OPENAI_API_KEY to keep a long-lived key out of the container."
        )

    wif_settings = OpenaiWifSettings()
    token_file = wif_settings.token_file
    interval = _refresh_interval()
    audience = _audience()
    refresher = _TokenFileRefresher(token_file, interval, audience)
    refresher.start()
    os.environ[OpenaiWifSettings.TOKEN_FILE_ENV] = str(token_file)
    _refresher = refresher
    log.info(
        "OpenAI auth: GCP Workload Identity Federation "
        "(audience %s, identity token file %s, refresh every %ss)",
        audience,
        token_file,
        interval,
    )
    return True
