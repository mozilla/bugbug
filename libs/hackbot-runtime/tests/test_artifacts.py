"""Tests for reading a parent run's artifacts back."""

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from hackbot_runtime import artifacts


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = self.path.encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_download_all_writes_each_artifact_under_its_key(tmp_path, server):
    artifacts.download_all(
        {"summary.json": f"{server}/a", "logs/job_1/log.txt": f"{server}/b"},
        tmp_path,
    )
    assert (tmp_path / "summary.json").read_text() == "/a"
    assert (tmp_path / "logs" / "job_1" / "log.txt").read_text() == "/b"


def test_download_all_refuses_a_key_outside_the_destination(tmp_path, server):
    with pytest.raises(ValueError, match="escapes"):
        artifacts.download_all({"../x": f"{server}/a"}, tmp_path / "dest")
