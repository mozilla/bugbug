"""Download AMO extensions and read their Firefox extension IDs."""

import json
import zipfile
from pathlib import Path

import requests

AMO_API_TMPL = "https://addons.mozilla.org/api/v5/addons/addon/{slug}/"
AMO_REQUEST_HEADERS = {"User-Agent": "webcompat-setup"}
AMO_API_TIMEOUT = 30
AMO_DOWNLOAD_TIMEOUT = 120


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
    with (
        amo_get(url, timeout=AMO_DOWNLOAD_TIMEOUT, stream=True) as resp,
        dest.open("wb") as f,
    ):
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
