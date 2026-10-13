"""Tests for the size guard on artifact downloads."""

import pytest
from app import gcs
from app.action_handlers import ArtifactTooLargeError


class _FakeBlob:
    """A blob whose size is only known after `reload()`, as with real GCS."""

    def __init__(self, size):
        self.size = None
        self._size = size
        self.downloaded = False

    def reload(self):
        self.size = self._size

    def download_as_bytes(self):
        self.downloaded = True
        return b"x" * self._size


@pytest.fixture
def blob(monkeypatch):
    blob = _FakeBlob(size=10)
    bucket = type("Bucket", (), {"blob": lambda self, name: blob})()
    client = type("Client", (), {"bucket": lambda self, name: bucket})()
    monkeypatch.setattr(gcs, "_client", lambda: client)
    return blob


async def test_download_at_the_limit(blob):
    assert await gcs.download_artifact_bytes("run-1", "k", max_bytes=10) == b"x" * 10


async def test_download_over_the_limit_raises_without_downloading(blob):
    with pytest.raises(ArtifactTooLargeError) as excinfo:
        await gcs.download_artifact_bytes("run-1", "k", max_bytes=9)
    assert excinfo.value.size == 10
    assert excinfo.value.max_bytes == 9
    assert not blob.downloaded


async def test_download_defaults_to_the_configured_limit(blob, monkeypatch):
    monkeypatch.setattr(gcs.settings, "action_apply_max_bytes", 9)
    with pytest.raises(ArtifactTooLargeError):
        await gcs.download_artifact_bytes("run-1", "k")
    assert not blob.downloaded


async def test_a_custom_limit_overrides_the_default(blob, monkeypatch):
    monkeypatch.setattr(gcs.settings, "action_apply_max_bytes", 9)
    assert await gcs.download_artifact_bytes("run-1", "k", max_bytes=10) == b"x" * 10
