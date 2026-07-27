"""Throwaway stand-in for hackbot-api, so the real hackbot-ui can be tried
locally without Cloud SQL / GCP. Serves just the endpoints the UI proxy calls:
GET /agents, GET /runs (with agent/status/author/limit/offset), GET /runs/{id}.
"""

import json
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEV_USER = "sledru@mozilla.com"
AGENTS = [
    "bug-fix",
    "autowebcompat-repro",
    "build-repair",
    "frontend-triage",
    "test-plan-generator",
]
STATUSES = ["succeeded", "failed", "running", "pending", "timed_out"]
# author, agent, status, inputs
_SEED = [
    (DEV_USER, "bug-fix", "succeeded", {"bug_id": 1889001}),
    (None, "bug-fix", "running", {"bug_id": 1889002, "revision_id": 412233}),
    ("padenot@mozilla.com", "frontend-triage", "succeeded", {"bug_id": 1890777}),
    (DEV_USER, "build-repair", "failed", {"git_commit": "a1b2c3d4e5f6a7b8"}),
    (DEV_USER, "frontend-triage", "running", {"bug_id": 1891555}),
    ("smujahid@mozilla.com", "test-plan-generator", "succeeded", {"feature_name": "WebGPU compute"}),
    (None, "bug-fix", "timed_out", {"bug_id": 1888120, "revision_id": 410900}),
    (DEV_USER, "autowebcompat-repro", "succeeded", {"bug_id": 1892003}),
    ("mcastelluccio@mozilla.com", "bug-fix", "failed", {"bug_id": 1887654}),
    (DEV_USER, "test-plan-generator", "pending", {"feature_name": "Cookie partitioning"}),
    (DEV_USER, "bug-fix", "succeeded", {"bug_id": 1893100}),
    ("smujahid@mozilla.com", "build-repair", "running", {"git_commit": "ffee00112233aabb"}),
    ("mcastelluccio@mozilla.com", "bug-fix", "succeeded", {"bug_id": 1885000, "revision_id": 409001}),
    (DEV_USER, "frontend-triage", "succeeded", {"bug_id": 1894222}),
    ("padenot@mozilla.com", "autowebcompat-repro", "timed_out", {"bug_id": 1886777}),
    (DEV_USER, "bug-fix", "failed", {"bug_id": 1895333}),
    (DEV_USER, "bug-fix", "running", {"bug_id": 1896444}),
    ("mcastelluccio@mozilla.com", "frontend-triage", "succeeded", {"bug_id": 1897555}),
    (None, "bug-fix", "succeeded", {"bug_id": 1884321, "revision_id": 408222}),
    (DEV_USER, "build-repair", "succeeded", {"git_commit": "0011223344556677"}),
]


def _build_runs():
    base = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)
    runs = []
    for i, (author, agent, status, inputs) in enumerate(_SEED):
        rid = f"{i:08x}-0000-4000-8000-{i:012x}"
        created = (base - timedelta(minutes=17 * i)).isoformat()
        err = None
        if status == "failed":
            err = "Agent exited non-zero: patch did not apply cleanly to tip."
        elif status == "timed_out":
            err = "Execution was cancelled or timed out"
        runs.append(
            {
                "run_id": rid,
                "agent": agent,
                "status": status,
                "inputs": inputs,
                "author": author,
                "created_at": created,
                "updated_at": created,
                "execution_name": None,
                "results_prefix": f"results/{rid}/",
                "summary": None,
                "artifacts": [],
                "error": err,
            }
        )
    return runs


RUNS = _build_runs()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quieter console
        pass

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)

        if path == "/agents":
            return self._json(
                [
                    {"name": a, "description": f"{a} agent", "input_schema": {}}
                    for a in AGENTS
                ]
            )

        if path.startswith("/runs/"):
            rid = path[len("/runs/") :]
            for r in RUNS:
                if r["run_id"] == rid:
                    return self._json(r)
            return self._json({"detail": "Run not found"}, 404)

        if path == "/runs":
            items = RUNS
            agent = q.get("agent", [None])[0]
            status = q.get("status", [None])[0]
            author = q.get("author", [None])[0]
            if agent:
                items = [r for r in items if r["agent"] == agent]
            if status:
                items = [r for r in items if r["status"] == status]
            if author:
                al = author.lower()
                items = [r for r in items if (r["author"] or "").lower() == al]
            offset = int(q.get("offset", ["0"])[0])
            limit = int(q.get("limit", ["50"])[0])
            return self._json(items[offset : offset + limit])

        return self._json({"detail": "Not found"}, 404)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
