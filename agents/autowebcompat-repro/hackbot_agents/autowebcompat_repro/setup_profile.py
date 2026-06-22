"""Build a Firefox profile, optionally preinstalling AMO extensions."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from collections.abc import Sequence
from pathlib import Path

AMO_API_TMPL = "https://addons.mozilla.org/api/v5/addons/addon/{slug}/"

# The MCP doesn't use the passed profile directly: it copies it into a
# firefox_devtools_mcp_profile/ subdir, but copies only prefs.js — not the
# extensions folder (see firefox-devtools-mcp src/firefox/profile.ts,
# resolveProfilePath). To bypass that we create the subdir ourselves; when it
# already exists the MCP just uses it as-is, extensions included.
MCP_PROFILE_DIR_NAME = "firefox_devtools_mcp_profile"


def resolve_xpi_url(slug: str) -> tuple[str, str]:
    """Return (download_url, version) for the latest signed xpi of an AMO addon."""
    url = AMO_API_TMPL.format(slug=slug)
    req = urllib.request.Request(url, headers={"User-Agent": "webcompat-setup"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    ver = data["current_version"]
    return ver["file"]["url"], ver["version"]


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "webcompat-setup"})
    with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as f:
        shutil.copyfileobj(resp, f)


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
    print(f"downloading {slug} {version} from AMO", file=sys.stderr)
    xpi_path = staging_dir / f".{slug}-download.xpi"
    download(url, xpi_path)
    ext_id = extract_extension_id(xpi_path)
    print(f"installing {slug} ({ext_id})", file=sys.stderr)
    install_xpi(profile_dir, xpi_path, ext_id)
    xpi_path.unlink(missing_ok=True)
    return ext_id


def warm_launch(firefox: str, profile_dir: Path, timeout: int = 15) -> None:
    """Run Firefox headless briefly so it scans + registers the dropped xpi."""
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
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
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


def setup_profile(firefox_path: str, extensions: Sequence[str] = ()) -> Path:
    """Build a profile with the given AMO extensions; return its parent dir.

    ``extensions`` is a list of AMO addon slugs (e.g. ``["chrome-mask"]``); each
    is downloaded and installed. With no extensions a plain profile is built and
    no warm launch happens. The returned path is meant to be passed as the
    devtools MCP's ``--profile-path`` (``build_devtools_server(profile_path=...)``).

    Raises ``RuntimeError`` if an extension does not end up registered and
    enabled in the profile.
    """
    parent = Path(tempfile.mkdtemp(prefix="ff-profile-"))
    profile_dir = parent / MCP_PROFILE_DIR_NAME
    profile_dir.mkdir(parents=True, exist_ok=True)

    installed = [
        install_amo_extension(profile_dir, parent, slug) for slug in extensions
    ]

    if not installed:
        return parent

    print("warm-launching Firefox to register the extensions", file=sys.stderr)
    warm_launch(firefox_path, profile_dir)
    time.sleep(1)

    for ext_id in installed:
        if not verify_registered(profile_dir, ext_id):
            raise RuntimeError(
                f"{ext_id} did not register and enable in {profile_dir}/extensions.json"
            )

    print(f"success — extensions registered in {profile_dir}", file=sys.stderr)
    return parent
