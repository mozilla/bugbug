"""Tests for GCS helpers: the artifact download size guard and input attachments."""

import pytest
from app import gcs
from app.action_handlers import ArtifactTooLargeError
from app.schemas import InputAttachment
from google.api_core.exceptions import PreconditionFailed


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


class _FakeInputBlob:
    """A blob that refuses to overwrite, as GCS does with `if_generation_match=0`."""

    def __init__(self, bucket, name):
        self._bucket = bucket
        self.name = name

    def upload_from_string(self, data, content_type, if_generation_match):
        assert if_generation_match == 0
        if self.name in self._bucket.objects:
            raise PreconditionFailed("exists")
        self._bucket.objects[self.name] = data
        self._bucket.uploads.append((self.name, content_type))


class _FakeInputsBucket:
    """An in-memory bucket that records each upload it accepts."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.uploads: list = []

    def blob(self, name):
        return _FakeInputBlob(self, name)

    def get_blob(self, name):
        if name not in self.objects:
            return None
        return type("Blob", (), {"size": len(self.objects[name])})()


@pytest.fixture
def inputs_bucket(monkeypatch):
    bucket = _FakeInputsBucket()
    client = type("Client", (), {"bucket": lambda self, name: bucket})()
    monkeypatch.setattr(gcs, "_client", lambda: client)
    monkeypatch.setattr(gcs.settings, "inputs_bucket", "hackbot-inputs")
    return bucket


async def test_inline_attachments_are_stored_by_content(inputs_bucket):
    [stored] = await gcs.store_input_attachments(
        [InputAttachment(name="shot.png", content_type="image/png", data_base64="YWJj")]
    )

    assert stored.key == gcs.input_attachment_key(b"abc")
    assert stored.size == 3
    assert stored.data_base64 is None
    assert inputs_bucket.objects == {stored.key: b"abc"}
    assert inputs_bucket.uploads == [(stored.key, "image/png")]


async def test_the_same_content_is_stored_once(inputs_bucket):
    first = InputAttachment(name="a.txt", data_base64="YWJj")
    second = InputAttachment(name="b.txt", data_base64="YWJj")

    stored = await gcs.store_input_attachments([first, second])

    assert stored[0].key == stored[1].key
    assert len(inputs_bucket.uploads) == 1


async def test_a_stored_attachment_is_checked_and_sized(inputs_bucket):
    key = gcs.input_attachment_key(b"abcd")
    inputs_bucket.objects[key] = b"abcd"

    [stored] = await gcs.store_input_attachments(
        [InputAttachment(name="a.txt", key=key, size=999)]
    )

    assert stored.size == 4
    assert inputs_bucket.uploads == []


async def test_a_missing_stored_attachment_raises(inputs_bucket):
    key = gcs.input_attachment_key(b"gone")
    with pytest.raises(gcs.InputAttachmentNotFoundError):
        await gcs.store_input_attachments([InputAttachment(name="a.txt", key=key)])


async def test_storing_attachments_needs_the_inputs_bucket(monkeypatch):
    monkeypatch.setattr(gcs.settings, "inputs_bucket", "")
    with pytest.raises(RuntimeError, match="inputs_bucket"):
        await gcs.store_input_attachments(
            [InputAttachment(name="a.txt", data_base64="YWJj")]
        )
