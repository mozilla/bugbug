"""Build a Firefox profile, optionally preinstalling AMO extensions."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Sequence
from pathlib import Path

import requests

logger = logging.getLogger("autowebcompat-repro")

AMO_API_TMPL = "https://addons.mozilla.org/api/v5/addons/addon/{slug}/"
AMO_REQUEST_HEADERS = {"User-Agent": "webcompat-setup"}
AMO_API_TIMEOUT = 30
AMO_DOWNLOAD_TIMEOUT = 120

REGISTER_TIMEOUT = 15
REGISTER_POLL_INTERVAL = 0.5

# The MCP doesn't use the passed profile directly: it copies it into a
# firefox_devtools_mcp_profile/ subdir, but copies only prefs.js — not the
# extensions folder (see firefox-devtools-mcp src/firefox/profile.ts,
# resolveProfilePath). To bypass that we create the subdir ourselves; when it
# already exists the MCP just uses it as-is, extensions included.
MCP_PROFILE_DIR_NAME = "firefox_devtools_mcp_profile"


def amo_get(
    url: str, *, timeout: int = AMO_API_TIMEOUT, stream: bool = False
) -> requests.Response:
    """Make an AMO HTTP GET with shared defaults and status handling."""
    resp = requests.get(
        url,
        headers=AMO_REQUEST_HEADERS,
        timeout=timeout,
        stream=stream,
    )
    resp.raise_for_status()
    return resp


def resolve_xpi_url(slug: str) -> tuple[str, str]:
    """Return (download_url, version) for the latest signed xpi of an AMO addon."""
    with amo_get(AMO_API_TMPL.format(slug=slug)) as resp:
        data = resp.json()
    ver = data["current_version"]
    return ver["file"]["url"], ver["version"]


def download(url: str, dest: Path) -> None:
    with amo_get(url, timeout=AMO_DOWNLOAD_TIMEOUT, stream=True) as resp:
        with dest.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if chunk:
                    f.write(chunk)


def extract_extension_id(xpi: Path) -> str:
    """Read the gecko extension ID out of the xpi's manifest.json."""
    with zipfile.ZipFile(xpi) as zf, zf.open("manifest.json") as f:
        manifest = json.load(f)
    for key in ("browser_specific_settings", "applications"):
        gecko = manifest.get(key, {}).get("gecko", {})
        if "id" in gecko:
            return gecko["id"]
    raise RuntimeError(f"no gecko extension ID in {xpi}'s manifest.json")


def install_xpi(profile_dir: Path, xpi: Path, ext_id: str) -> None:
    """Drop the xpi into the profile's extensions dir under its gecko ID.

    Firefox registers an extension found in ``extensions/`` as *disabled*,
    pending the sideload-approval doorhanger — which a headless launch can't
    click, leaving it ``userDisabled``. ``extensions.autoDisableScopes=0`` tells
    Firefox to auto-enable profile-scope extensions instead, so the warm launch
    brings them up active.
    """
    ext_dir = profile_dir / "extensions"
    ext_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(xpi, ext_dir / f"{ext_id}.xpi")
    (profile_dir / "user.js").write_text(
        'user_pref("extensions.autoDisableScopes", 0);\n'
        'user_pref("extensions.enabledScopes", 15);\n'
    )


def install_amo_extension(profile_dir: Path, staging_dir: Path, slug: str) -> str:
    """Download an AMO addon by slug and install it; return its gecko ID.

    ``staging_dir`` holds the xpi during download and is not the profile, so the
    download artifact isn't mistaken for a profile file; it's removed afterwards.
    """
    url, version = resolve_xpi_url(slug)
    logger.info("downloading %s %s from AMO", slug, version)
    xpi_path = staging_dir / f".{slug}-download.xpi"
    download(url, xpi_path)
    ext_id = extract_extension_id(xpi_path)
    logger.info("installing %s (%s)", slug, ext_id)
    install_xpi(profile_dir, xpi_path, ext_id)
    xpi_path.unlink(missing_ok=True)
    return ext_id


def warm_launch(
    firefox: str,
    profile_dir: Path,
    ext_ids: Sequence[str] = (),
    timeout: int = REGISTER_TIMEOUT,
) -> None:
    """Run Firefox headless until the dropped xpis register or timeout expires."""
    proc = subprocess.Popen(
        [
            firefox,
            "--profile",
            str(profile_dir),
            "-headless",
            "-no-remote",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if ext_ids:
            wait_until_registered(profile_dir, ext_ids, timeout=timeout)
        else:
            proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass
    finally:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def verify_registered(profile_dir: Path, ext_id: str) -> bool:
    """True only if the extension is registered AND enabled.

    Firefox can register a sideloaded extension while leaving it disabled
    (``active`` false / ``userDisabled`` true) pending approval, in which case
    it won't actually load — so an ``active`` check is required, not just
    presence in ``extensions.json``.
    """
    ext_json = profile_dir / "extensions.json"
    if not ext_json.exists():
        return False
    try:
        data = json.loads(ext_json.read_text())
    except json.JSONDecodeError:
        return False
    return any(
        a.get("id") == ext_id and a.get("active") and not a.get("userDisabled")
        for a in data.get("addons", [])
    )


def wait_until_registered(
    profile_dir: Path,
    ext_ids: Sequence[str],
    timeout: int = REGISTER_TIMEOUT,
) -> None:
    """Poll until every extension is registered + enabled, or raise on timeout."""
    deadline = time.monotonic() + timeout
    pending = list(ext_ids)
    while pending:
        pending = [
            ext_id for ext_id in pending if not verify_registered(profile_dir, ext_id)
        ]
        if not pending:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"{', '.join(pending)} did not register and enable in "
                f"{profile_dir}/extensions.json within {timeout:g}s"
            )
        time.sleep(REGISTER_POLL_INTERVAL)


def setup_profile(firefox_path: str, extensions: Sequence[str] = ()) -> Path:
    """Build a profile with the given AMO extensions; return its parent dir.

    ``extensions`` is a list of AMO addon slugs (e.g. ``["chrome-mask"]``); each
    is downloaded and installed. With no extensions an empty profile parent is
    returned and no warm launch happens. The returned path is meant to be passed as the
    devtools MCP's ``--profile-path`` (``build_devtools_server(profile_path=...)``).

    Raises ``RuntimeError`` if an extension does not end up registered and
    enabled in the profile.
    """
    parent = Path(tempfile.mkdtemp(prefix="ff-profile-"))

    if not extensions:
        return parent

    try:
        profile_dir = parent / MCP_PROFILE_DIR_NAME
        profile_dir.mkdir(parents=True, exist_ok=True)

        installed = [
            install_amo_extension(profile_dir, parent, slug) for slug in extensions
        ]

        logger.info("warm-launching Firefox to register the extensions")
        warm_launch(firefox_path, profile_dir, installed)

        logger.info("extensions registered in %s", profile_dir)
        return parent
    except Exception:
        shutil.rmtree(parent, ignore_errors=True)
        raise
