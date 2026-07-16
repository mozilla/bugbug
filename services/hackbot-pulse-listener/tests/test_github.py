from unittest.mock import MagicMock, patch

import httpx
from app import github


def _resp(payload):
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = payload
    return resp


def test_repo_slug_from_firefox_git_url():
    assert github._repo_slug() == "mozilla-firefox/firefox"


def test_commit_author_email_returns_author():
    payload = {"commit": {"author": {"email": "dev@mozilla.com"}}}
    with patch.object(github.httpx, "get", return_value=_resp(payload)) as get:
        assert github.commit_author_email("abc123") == "dev@mozilla.com"
    assert "mozilla-firefox/firefox/commits/abc123" in get.call_args.args[0]


def test_commit_author_email_none_on_http_error():
    with patch.object(github.httpx, "get", side_effect=httpx.HTTPError("boom")):
        assert github.commit_author_email("abc123") is None


def test_commit_author_email_none_when_missing():
    with patch.object(github.httpx, "get", return_value=_resp({"commit": {}})):
        assert github.commit_author_email("abc123") is None
