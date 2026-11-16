"""Fetch the internal top-sites rankings the calculator's --classify-domain reads.

The rankings are internal data, so they are in neither the calculator package nor
this image. In production the job downloads them from GCS at start-up
($PERF_TRIAGE_TOP_SITES_URI); a local run points $PERF_TRIAGE_TOP_SITES at a file.
Without either the skill cannot pick pages-affected from traffic, which changes
ratings, so the run stops rather than triaging without them.
"""

import os
import tempfile
from pathlib import Path

from perf_triage_calculator.perf_triage import TOP_SITES_ENV

TOP_SITES_URI_ENV = "PERF_TRIAGE_TOP_SITES_URI"


def _download(uri: str, dest: Path) -> None:
    from google.cloud import storage

    bucket, _, name = uri.removeprefix("gs://").partition("/")
    storage.Client().bucket(bucket).blob(name).download_to_filename(dest)


def ensure_top_sites() -> str:
    """Make the rankings available and set $PERF_TRIAGE_TOP_SITES; return the path."""
    uri = os.environ.get(TOP_SITES_URI_ENV)
    if uri:
        if not uri.startswith("gs://") or "/" not in uri.removeprefix("gs://"):
            raise RuntimeError(
                f"{TOP_SITES_URI_ENV} must be gs://bucket/object, got {uri!r}"
            )
        dest = Path(tempfile.mkdtemp(prefix="top-sites-")) / "top_sites.json"
        _download(uri, dest)
        os.environ[TOP_SITES_ENV] = str(dest)
    path = os.environ.get(TOP_SITES_ENV)
    if not path or not Path(path).is_file():
        raise RuntimeError(
            f"top-sites rankings not found: set {TOP_SITES_URI_ENV} (gs://...) "
            f"or {TOP_SITES_ENV} (a local file)"
        )
    return path
