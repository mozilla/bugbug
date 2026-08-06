"""Small shared Lando API client."""

from __future__ import annotations

import base64

import httpx

from lando_client.config import LandoSettings

TREEHERDER_URL = "https://treeherder.mozilla.org"

# The patch formats Lando accepts (`PatchFormat` in lando.main.scm.helpers).
PATCH_FORMAT_GIT = "git-format-patch"
PATCH_FORMAT_HG = "hgexport"


class LandoAPIError(Exception):
    """Lando rejected a request (or answered with something unusable)."""


def encode_patch(patch: bytes) -> str:
    """Base64-encode one patch for the ``patches`` array."""
    return base64.b64encode(patch).decode("ascii")


class LandoClient:
    def __init__(self, settings: LandoSettings | None = None) -> None:
        self.settings = settings or LandoSettings.from_env()

    @property
    def base_url(self) -> str:
        return self.settings.url.rstrip("/")

    @property
    def try_patches_url(self) -> str:
        return f"{self.base_url}/api/try/patches"

    def job_url(self, job_id: int) -> str:
        """Lando's own page for a landing job (its live status)."""
        return f"{self.base_url}/landings/{job_id}"

    def treeherder_url(self, job_id: int, repo_name: str = "try") -> str:
        """Treeherder's view of a Lando try job.

        Keyed by ``landoCommitID`` rather than a revision because the push has
        no revision yet: Lando applies the patches asynchronously, so this URL
        is valid (if initially empty) the moment the job is created.
        """
        return (
            f"{TREEHERDER_URL}/jobs?repo={repo_name}"
            f"&landoInstance={self.settings.instance_id}"
            f"&landoCommitID={job_id}"
        )

    async def submit_try_patches(
        self,
        patches: list[str],
        base_commit: str,
        *,
        base_commit_vcs: str = "git",
        patch_format: str = PATCH_FORMAT_GIT,
        repo_name: str = "try",
    ) -> int:
        """Submit a base64-encoded patch series to a Try repo; return the job id.

        ``patches`` are applied in order on top of ``base_commit``, which must be
        a full 40-character hash of a *published* commit (Lando maps it to the
        try repo's own SCM when ``base_commit_vcs`` differs, so a firefox git
        sha is fine for the Mercurial ``try``).
        """
        payload = {
            "repo_name": repo_name,
            "base_commit": base_commit,
            "base_commit_vcs": base_commit_vcs,
            "patch_format": patch_format,
            "patches": patches,
        }
        async with httpx.AsyncClient(timeout=self.settings.timeout_seconds) as client:
            response = await client.post(
                self.try_patches_url,
                json=payload,
                headers={"Authorization": f"Bearer {self.settings.access_token}"},
            )

        if response.status_code >= 400:
            raise LandoAPIError(_error_detail(response))

        try:
            return int(response.json()["id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise LandoAPIError(
                f"Lando accepted the push ({response.status_code}) but returned no "
                "job id"
            ) from exc


def _error_detail(response: httpx.Response) -> str:
    """A readable message for a failed Lando response.

    Lando reports errors as RFC 7807 problem details (``title``/``detail``),
    but a proxy or a 5xx can still answer with HTML, so fall back to the status
    line rather than raising a ``JSONDecodeError`` over the real failure.
    """
    try:
        problem = response.json()
    except ValueError:
        problem = None

    if isinstance(problem, dict):
        parts = [str(problem[key]) for key in ("title", "detail") if problem.get(key)]
        if parts:
            return f"Lando returned HTTP {response.status_code}: {': '.join(parts)}"

    return (
        f"Lando returned HTTP {response.status_code} ({response.reason_phrase}) "
        f"for {response.request.url}"
    )
