# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Public Bugzilla, Perfherder and CI evidence with explicit provenance.

Profile discovery follows foofrix's hunt-ci-profiles.ts: match the exact platform,
use successful profiling jobs for the affected suite
on either side of the alert push. Nearby profiles are diagnostic evidence only.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from agent_tools.bugzilla import (
    BugzillaContext,
    get_bug_attachments,
    get_bug_comments,
    get_bugs,
)
from agent_tools.mozilla_vcs import MozillaVcsContext, get_commit_diff, get_commit_info
from agent_tools.registry import ToolError
from hackbot_runtime import AgentError

TREEHERDER = "https://treeherder.mozilla.org"
TASKCLUSTER = "https://firefox-ci-tc.services.mozilla.com"
BUGZILLA = "https://bugzilla.mozilla.org/rest"
LANDO = "https://lando.moz.tools"
COMPACT_PROFILE = "public/test_info/profile_speedometer3_compact.jslb.gz"
ZIP_PROFILE = "public/test_info/profile_speedometer3.zip"
REPOSITORIES = {
    "autoland": "integration/autoland",
    "mozilla-central": "mozilla-central",
}
HARNESS_INFRA_PATHS = (
    "testing/",
    "python/mozperftest/",
    "tools/tryselect/",
    "taskcluster/",
)
MAX_CULPRIT_CHANGESETS = 50
MAX_CULPRIT_PATCH_BYTES = 2_000_000
# Individual Perfherder alert statuses, distinct from alert-summary statuses.
# These attribute a row to another summary or mark it as noise.
DOWNSTREAM, REASSIGNED, INVALID, INFRA = 1, 2, 3, 5
MAX_METRICS = 3
# Rows considered when earlier ones cannot be validated (no task or history).
MAX_CANDIDATES = 10
# Historical baselines: widen past the alert's own pushes when they have too
# few jobs. Bad data stays close to the culprit so a backout is not included.
MIN_BASELINE_JOBS = 2
GOOD_BASELINE_JOBS = 5
MAX_GOOD_PUSHES = 5
MAX_BAD_PUSHES = 3


class EvidenceError(AgentError):
    """Missing or incompatible evidence; this is never a positive verdict."""


class EvidencePending(EvidenceError):
    """Evidence that Perfherder may still attach shortly; worth retrying."""


class OutOfScope(EvidenceError):
    """The alert has nothing in the suites this run may investigate."""


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def metric_key(signature: dict) -> str:
    """Keep OS/build/options/subtest identities distinct across repositories."""
    return json.dumps(
        [
            signature["framework_id"],
            signature["machine_platform"],
            signature["suite"],
            signature.get("test", ""),
            signature.get("application", "firefox"),
            signature.get("option_collection_hash", ""),
            sorted(o for o in signature.get("extra_options", []) if o),
            signature.get("lower_is_better", True),
            signature.get("measurement_unit") or "",
        ],
        separators=(",", ":"),
    )


def regression_candidates(alert: dict, suites: list[str] | None = None) -> list[dict]:
    """Eligible regression rows, largest reported change first.

    Raises OutOfScope without any, or with ``suites`` and none of those suites.
    """
    rows = []
    own = [
        row
        for row in alert.get("alerts", [])
        if row.get("status") not in {DOWNSTREAM, REASSIGNED, INVALID, INFRA}
    ]
    reassigned = [
        row
        for row in alert.get("related_alerts", [])
        if row.get("status") == REASSIGNED
    ]
    for row in [*own, *reassigned]:
        sig = row.get("series_signature", {})
        if row.get("is_regression") is True and sig.get("suite"):
            rows.append(row)
    if not rows:
        raise OutOfScope("The alert contains no regression measurements")
    # Different metrics have different units; compare the reported percentages.
    # Sort before deduplication so repeated signatures retain their largest drop.
    rows.sort(
        key=lambda r: (
            -float(r.get("amount_pct") or 0),
            metric_key(r["series_signature"]),
        )
    )
    if suites:
        allowed = {suite.lower() for suite in suites}
        found = sorted({r["series_signature"]["suite"] for r in rows})
        rows = [r for r in rows if r["series_signature"]["suite"].lower() in allowed]
        if not rows:
            raise OutOfScope(
                f"The alert has no regression in {', '.join(sorted(allowed))} "
                f"(it regressed {', '.join(found)}); not investigated"
            )
    unique = {}
    for row in rows:
        unique.setdefault(metric_key(row["series_signature"]), row)
    return list(unique.values())


def regression_rows(alert: dict) -> list[dict]:
    return regression_candidates(alert)[:MAX_METRICS]


def label_matches_platform(name: str, platform: str) -> bool:
    """Raptor/talos labels start with test-<platform>/; mozperftest's end with it."""
    prefix = name.split("/", 1)[0]
    return name.startswith(f"test-{platform}/") or (
        "/" in name and prefix.endswith(f"-{platform}")
    )


def metric_label(row: dict) -> str:
    sig = row["series_signature"]
    return f"{sig['machine_platform']} {sig['suite']} {sig.get('test', '')}".strip()


def baselines(
    alert: dict,
    rows: list[dict],
    history: dict,
    anchors: dict[int, tuple[int, int]] | None = None,
) -> tuple[dict, dict, dict]:
    """Good/bad observations per metric from the alert's pushes and neighbours.

    Good data comes from the newest pushes before the alert push until there are
    GOOD_BASELINE_JOBS jobs; bad data from the alert push, extended to the next
    pushes only while it has fewer than MIN_BASELINE_JOBS jobs.

    ``anchors`` maps a reassigned row's id to the (timestamp, push id) its own
    alert was raised at. Its bad data may start at any push up to that one, for
    platforms that do not run on every push.
    """
    origin = (alert["push_timestamp"], alert["push_id"])
    good, bad, provenance = {}, {}, {}
    points_by_signature: dict[int, list[dict]] = {}
    for group in history.values():
        for point in group:
            # Old points can outlive their job records (job_id is null).
            if point.get("job_id") is not None:
                points_by_signature.setdefault(point["signature_id"], []).append(point)
    for row in rows:
        key = metric_key(row["series_signature"])
        pushes: dict[tuple[int, int], dict[int, dict]] = {}
        for point in points_by_signature.get(row["series_signature"]["id"], []):
            push = (point["push_timestamp"], point["push_id"])
            pushes.setdefault(push, {})[point["job_id"]] = point
        before = sorted((p for p in pushes if p < origin), reverse=True)
        after = sorted(p for p in pushes if p >= origin)

        def take(order, target, limit, pushes=pushes):
            chosen, jobs = [], []
            for push in order[:limit]:
                if len(jobs) >= target:
                    break
                chosen.append(push[1])
                jobs.extend(pushes[push].values())
            return chosen, jobs

        good_pushes, good[key] = take(before, GOOD_BASELINE_JOBS, MAX_GOOD_PUSHES)
        if after and after[0] > max(origin, (anchors or {}).get(row["id"], origin)):
            after = []  # No data by the push the regression was seen at.
        bad_pushes, bad[key] = take(after, MIN_BASELINE_JOBS, MAX_BAD_PUSHES)
        provenance[key] = {"good_push_ids": good_pushes, "bad_push_ids": bad_pushes}
    return good, bad, provenance


def find_alert_ids(comments: list[dict]) -> list[int]:
    text = "\n".join(c.get("text", c.get("raw_text", "")) for c in comments)
    patterns = (
        r"treeherder\.mozilla\.org/perfherder/alerts\?[^\s)\]]*\bid=(\d+)",
        r"mach try perf --alert\s+(\d+)",
    )
    return sorted({int(m) for pattern in patterns for m in re.findall(pattern, text)})


class EvidenceClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def get(self, url: str, **params) -> object:
        # Retry reads only. POSTing a try submission is never retried implicitly.
        for attempt in range(3):
            try:
                response = await self.client.get(url, params=params)
                response.raise_for_status()
                return response.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if (
                    isinstance(exc, httpx.HTTPStatusError)
                    and exc.response.status_code < 500
                    and exc.response.status_code != 429
                ):
                    raise
                if attempt == 2:
                    raise
                await asyncio.sleep(2**attempt)
        raise AssertionError("unreachable")

    async def request(self, path: str, *, params: dict) -> dict:
        """Bugzilla read transport for the shared tools, retaining read retries."""
        return await self.get(f"{BUGZILLA}/{path}", **params)

    async def bug(self, bug_id: int) -> dict:
        ctx = BugzillaContext(client=self)
        data = await get_bugs(
            ctx, [bug_id], include_fields="_all,regressed_by,keywords,whiteboard"
        )
        if data["count"] != 1 or data["bugs"][0]["id"] != bug_id:
            raise EvidenceError(f"Public Bugzilla bug {bug_id} was not returned")
        comments = await get_bug_comments(ctx, bug_id)
        attachments = await get_bug_attachments(ctx, bug_id)
        return {
            "bug": data["bugs"][0],
            "comments": comments["comments"],
            "attachments": attachments["attachments"],
        }

    async def alert(self, alert_id: int, bug_id: int) -> dict:
        alert = await self.get(f"{TREEHERDER}/api/performance/alertsummary/{alert_id}/")
        if alert.get("bug_number") is None:
            raise EvidencePending(f"Alert {alert_id} is not linked to a bug yet")
        if alert.get("bug_number") != bug_id:
            raise EvidenceError(f"Alert {alert_id} does not belong to bug {bug_id}")
        if alert.get("repository") not in REPOSITORIES:
            raise EvidenceError(
                "Only autoland and mozilla-central alerts are supported"
            )
        for name in ("revision", "prev_push_revision"):
            if not re.fullmatch(r"[0-9a-f]{40}", alert.get(name, "")):
                raise EvidenceError(f"Alert has no full {name}")
        regression_rows(alert)
        return alert

    async def git_revision(self, hg_revision: str) -> str:
        value = await self.get(f"{LANDO}/api/hg2git/firefox/{hg_revision}")
        revision = value.get("git_hash", "")
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise EvidenceError(f"No Git mapping for {hg_revision}")
        return revision

    async def history(self, alert: dict, rows: list[dict]) -> dict:
        """Every point for these signatures from a week before to two days after."""
        timestamp = datetime.fromtimestamp(alert["push_timestamp"], timezone.utc)
        return await self.get(
            f"{TREEHERDER}/api/project/{alert['repository']}/performance/data/",
            signature_id=[r["series_signature"]["id"] for r in rows],
            start_date=(timestamp - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S"),
            end_date=(timestamp + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S"),
        )

    async def anchors(
        self, alert: dict, rows: list[dict]
    ) -> dict[int, tuple[int, int]]:
        """The push each reassigned row's own alert was raised at, same repository."""
        result = {}
        for summary_id in sorted(
            {
                r["summary_id"]
                for r in rows
                if r.get("status") == REASSIGNED
                and r.get("summary_id") not in (None, alert["id"])
            }
        ):
            summary = await self.get(
                f"{TREEHERDER}/api/performance/alertsummary/{summary_id}/"
            )
            if summary.get("repository") != alert["repository"]:
                continue
            for row in rows:
                if row.get("summary_id") == summary_id:
                    result[row["id"]] = (summary["push_timestamp"], summary["push_id"])
        return result

    async def row_tasks(
        self, alert: dict, rows: list[dict], history: dict | None = None
    ) -> dict[int, str]:
        """Map each row's id to the non-profiling CI task producing it, if any."""
        data = await self.get(
            f"{TREEHERDER}/api/performance/alertsummary-tasks/", id=alert["id"]
        )
        # Only test jobs can be repeated to measure a candidate; build-size
        # metrics come from build tasks and are not investigated.
        allowed = {
            t
            for t in data["tasks"]
            if t and t.startswith(("test-", "perftest-")) and "profil" not in t
        }
        labels = {}

        async def label(url: str) -> str | None:
            if url not in labels:
                try:
                    job = await self.get(url)
                    labels[url] = job.get("job_type_name") or job.get(
                        "metadata", {}
                    ).get("name")
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 404:
                        raise
                    labels[url] = None
            return labels[url]

        resolved = {}
        for row in rows:
            sig = row["series_signature"]
            platform = sig["machine_platform"]
            chosen = None
            for field in ("taskcluster_metadata", "prev_taskcluster_metadata"):
                task_id = (row.get(field) or {}).get("task_id")
                if not task_id:
                    continue
                name = await label(f"{TASKCLUSTER}/api/queue/v1/task/{task_id}")
                if name in allowed and label_matches_platform(name, platform):
                    chosen = name
                    break
            if chosen is None:
                # Some alerts omit task metadata. Map the exact signature's
                # historical points back to Treeherder jobs, nearest push first,
                # instead of guessing from suite names.
                if history is None:
                    history = await self.history(alert, rows)
                matches = sorted(
                    (
                        p
                        for group in history.values()
                        for p in group
                        if p["signature_id"] == sig["id"]
                        and p.get("job_id") is not None
                    ),
                    key=lambda p: (
                        abs(p["push_timestamp"] - alert["push_timestamp"]),
                        p["job_id"],
                    ),
                )
                for point in matches[:5]:
                    name = await label(
                        f"{TREEHERDER}/api/project/{alert['repository']}/jobs/{point['job_id']}/"
                    )
                    if name in allowed and label_matches_platform(name, platform):
                        chosen = name
                        break
            if chosen is not None:
                resolved[row["id"]] = chosen
        return resolved

    async def tasks(self, alert: dict, rows: list[dict]) -> list[str]:
        """The CI tasks for exactly these rows; every row must resolve."""
        resolved = await self.row_tasks(alert, rows)
        for row in rows:
            if row["id"] not in resolved:
                raise EvidenceError(
                    f"No non-profiling task for selected regression {metric_label(row)}"
                )
        return sorted(set(resolved.values()))

    async def select(
        self, alert: dict, history: dict, suites: list[str] | None = None
    ) -> tuple[list, list, list]:
        """Pick up to three metrics that can be measured on Try and validated.

        Rows without a resolvable CI task or without enough historical jobs are
        skipped in favour of the next-largest regression, and reported.
        """
        candidates = regression_candidates(alert, suites)[:MAX_CANDIDATES]
        good, bad, _ = baselines(
            alert, candidates, history, await self.anchors(alert, candidates)
        )
        excluded, usable = [], []
        for row in candidates:
            key = metric_key(row["series_signature"])
            counts = (len(good[key]), len(bad[key]))
            if min(counts) < MIN_BASELINE_JOBS:
                excluded.append(
                    {
                        "row_id": row["id"],
                        "metric": metric_label(row),
                        "reason": "Not enough historical jobs to validate "
                        f"(good {counts[0]}, bad {counts[1]}; need {MIN_BASELINE_JOBS})",
                    }
                )
            else:
                usable.append(row)
        resolved = await self.row_tasks(alert, usable, history)
        rows = []
        for row in usable:
            if row["id"] not in resolved:
                excluded.append(
                    {
                        "row_id": row["id"],
                        "metric": metric_label(row),
                        "reason": "No non-profiling CI task produces this metric",
                    }
                )
            elif len(rows) < MAX_METRICS:
                rows.append(row)
        if not rows:
            raise EvidenceError(
                "No regression metric in the alert can be measured and validated: "
                + "; ".join(f"{e['metric']}: {e['reason']}" for e in excluded)
            )
        return rows, sorted({resolved[r["id"]] for r in rows}), excluded

    async def selected(self, alert: dict, row_ids: list[int]) -> tuple[list, list]:
        """Re-derive a previous selection; the rows must still be eligible."""
        try:
            by_id = {row["id"]: row for row in regression_candidates(alert)}
        except EvidenceError:
            by_id = {}
        missing = [i for i in row_ids if i not in by_id]
        if missing or not 1 <= len(row_ids) <= MAX_METRICS:
            raise EvidenceError(
                f"Selected alert rows {missing or row_ids} are no longer eligible regressions"
            )
        rows = [by_id[i] for i in row_ids]
        return rows, await self.tasks(alert, rows)

    async def baselines(self, alert: dict, rows: list[dict]) -> dict:
        history = await self.history(alert, rows)
        good, bad, provenance = baselines(
            alert, rows, history, await self.anchors(alert, rows)
        )
        return {
            "repository": alert["repository"],
            "good": good,
            "bad": bad,
            "pushes": provenance,
        }

    async def push(self, repo: str, **params) -> dict:
        data = await self.get(f"{TREEHERDER}/api/project/{repo}/push/", **params)
        rows = data["results"]
        if len(rows) != 1:
            raise EvidenceError(
                f"Expected one {repo} push matching {params}, got {len(rows)}"
            )
        return rows[0]

    async def measurements(self, repo: str, push_id: int, rows: list[dict]) -> dict:
        """Return per-job points, matched by complete signature, never just a name."""
        points = await self.get(
            f"{TREEHERDER}/api/project/{repo}/performance/data/", push_id=push_id
        )
        ids = sorted({p["signature_id"] for group in points.values() for p in group})
        if not ids:
            return {}
        signatures = {}
        for offset in range(0, len(ids), 100):
            batch = await self.get(
                f"{TREEHERDER}/api/project/{repo}/performance/signatures/",
                id=ids[offset : offset + 100],
            )
            signatures.update(batch)
        wanted = {metric_key(row["series_signature"]) for row in rows}
        result = {}
        for group in points.values():
            for point in group:
                sig = signatures.get(str(point["signature_id"]))
                if sig is None or point["push_id"] != push_id:
                    continue
                key = metric_key(sig)
                if key in wanted:
                    # One observation per CI job, not correlated browser iterations.
                    result.setdefault(key, {})[point["job_id"]] = point
        return {key: list(jobs.values()) for key, jobs in result.items()}

    async def download_profile(
        self, task_id: str, retry: int, dest: Path, *, suite: str = "speedometer3"
    ) -> dict:
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]{22}", task_id)
            or type(retry) is not int
            or retry < 0
        ):
            raise EvidenceError("Invalid profile task identity")
        if suite.lower() == "speedometer3":
            artifacts = [COMPACT_PROFILE, ZIP_PROFILE]
        else:
            artifacts = []
            token = None
            while True:
                params = {"continuationToken": token} if token else {}
                listing = await self.get(
                    f"{TASKCLUSTER}/api/queue/v1/task/{task_id}/runs/{retry}/artifacts",
                    **params,
                )
                for item in listing.get("artifacts", []):
                    name = item["name"]
                    if (
                        name.startswith("public/")
                        and "profile" in name.lower()
                        and "resource-usage" not in name
                        and name.endswith((".json", ".json.gz", ".jslb.gz", ".zip"))
                    ):
                        artifacts.append(name)
                next_token = listing.get("continuationToken")
                if not next_token:
                    break
                if next_token == token:
                    raise EvidenceError("Profile artifact pagination made no progress")
                token = next_token
            artifacts.sort(
                key=lambda n: (
                    suite.lower() not in n.lower(),
                    "compact" not in n,
                    not n.endswith(".jslb.gz"),
                    n,
                )
            )
        for artifact in artifacts:
            url = f"{TASKCLUSTER}/api/queue/v1/task/{task_id}/runs/{retry}/artifacts/{artifact}"
            path = dest / f"{task_id}-{retry}-{quote(artifact, safe='')}"
            if not path.exists():
                async with self.client.stream(
                    "GET",
                    url,
                    follow_redirects=True,
                    headers={"Accept-Encoding": "identity"},
                ) as response:
                    if response.status_code == 404:
                        continue
                    response.raise_for_status()
                    partial = path.with_suffix(path.suffix + ".partial")
                    size = 0
                    try:
                        with partial.open("wb") as output:
                            async for chunk in response.aiter_raw():
                                size += len(chunk)
                                if size > 1024**3:
                                    raise EvidenceError(
                                        "Profile exceeds 1 GiB download limit"
                                    )
                                output.write(chunk)
                        partial.replace(path)
                    finally:
                        partial.unlink(missing_ok=True)
            return {
                "task_id": task_id,
                "retry_id": retry,
                "url": url,
                "path": str(path),
            }
        raise EvidenceError(
            f"No compatible Firefox profile artifact for task {task_id}, run {retry}"
        )

    async def profile_pair(self, alert: dict, row: dict, dest: Path) -> dict:
        signature = row["series_signature"]
        platform = signature["machine_platform"]
        suite = signature["suite"]
        speedometer = suite.lower() == "speedometer3"
        download_options = {} if speedometer else {"suite": suite}
        # Resolve the same measurement label used for try selection. Removing
        # only profiling modifiers preserves application, cold/warm and runtime
        # variants such as no-nv, which share the same opt build hash.
        measurement_task = (await self.tasks(alert, [row]))[0]
        configuration = {
            "application": signature.get("application", "firefox"),
            "extra_options": sorted(signature.get("extra_options", [])),
            "measurement_task": measurement_task,
        }
        expected_revisions = {
            "before": alert.get("prev_push_revision"),
            "after": alert.get("revision"),
        }
        repo = "autoland"
        if alert["repository"] == repo:
            target_push = await self.push(repo, id=alert["push_id"])
        else:
            target_push = await self.push(repo, revision=alert["revision"])
        target = target_push["id"]
        # Push IDs are shared by every repository, so walk autoland's own pushes
        # by time: the 30 before the alert push, and it plus the 29 after it.
        # The push list is newest first.
        moment = target_push["push_timestamp"]
        earlier = await self.get(
            f"{TREEHERDER}/api/project/{repo}/push/",
            push_timestamp__lt=moment,
            count=30,
        )
        later = await self.get(
            f"{TREEHERDER}/api/project/{repo}/push/",
            push_timestamp__gte=moment,
            push_timestamp__lte=moment + 3 * 24 * 60 * 60,
            count=1000,
        )
        before_pushes = sorted(
            (p for p in earlier["results"] if p["id"] != target),
            key=lambda p: (p["push_timestamp"], p["id"]),
            reverse=True,
        )[:30]
        # The alert push is distance 0 even when another push shares its time.
        after_pushes = sorted(
            [target_push, *(p for p in later["results"] if p["id"] != target)],
            key=lambda p: (p["push_timestamp"], p["id"] != target, p["id"]),
        )[:30]
        pushes = {}
        distance = {}
        for direction, side in ((-1, before_pushes), (1, after_pushes)):
            for index, push in enumerate(side):
                pushes[push["id"]] = push
                distance[push["id"]] = direction * (index + (direction < 0))
        jobs = []
        for side in (before_pushes, after_pushes):
            # Bounded pagination: at most 10 pages of 2000 jobs per side.
            for page_number in range(10):
                data = await self.get(
                    f"{TREEHERDER}/api/project/{repo}/jobs/",
                    push_id__in=",".join(str(p["id"]) for p in side),
                    platform=platform,
                    state="completed",
                    result="success",
                    count=2000,
                    offset=page_number * 2000,
                    **({"job_type_symbol": "sp3-p"} if speedometer else {}),
                )
                page = data["results"]
                jobs.extend(
                    j
                    for j in page
                    if j["push_id"] in pushes
                    and j.get("platform") == platform
                    and (
                        not signature.get("option_collection_hash")
                        or j.get(
                            "option_collection_hash",
                            signature["option_collection_hash"],
                        )
                        == signature["option_collection_hash"]
                    )
                    and j.get("state") == "completed"
                    and j.get("result") == "success"
                    and re.sub(
                        r"-(?:native-profiling|profiling|gecko-profile)(?=-|$)",
                        "",
                        j.get("job_type_name", ""),
                    )
                    == measurement_task
                    and (
                        "native-profiling" in j.get("job_type_name", "")
                        if speedometer
                        else "profil" in j.get("job_type_name", "")
                    )
                )
                if len(page) < 2000:
                    break

        def record_provenance(pair: dict) -> dict:
            for side, expected in expected_revisions.items():
                pair[side]["expected_revision"] = expected
            pair["nearby"] = any(
                not expected or pair[side]["revision"] != expected
                for side, expected in expected_revisions.items()
            )
            return pair

        async def from_jobs(candidates: list[dict], kind: str) -> dict | None:
            before = sorted(
                [j for j in candidates if distance[j["push_id"]] < 0],
                key=lambda j: (-distance[j["push_id"]], j["id"]),
            )
            after = sorted(
                [j for j in candidates if distance[j["push_id"]] >= 0],
                key=lambda j: (distance[j["push_id"]], j["id"]),
            )
            pair = {
                "platform": platform,
                "suite": suite,
                "profile_kind": kind,
                "nearby": True,
                "repository": repo,
                "target_push_id": target,
                **configuration,
            }
            for side, choices in (("before", before), ("after", after)):
                for job in choices:
                    try:
                        profile = await self.download_profile(
                            job["task_id"],
                            job.get("retry_id", 0),
                            dest,
                            **download_options,
                        )
                    except EvidenceError, httpx.HTTPError:
                        continue
                    push = pushes[job["push_id"]]
                    pair[side] = {
                        **profile,
                        "job": job,
                        "push": push,
                        "revision": push["revision"],
                        "push_distance": distance[job["push_id"]],
                    }
                    break
                else:
                    return None
            return record_provenance(pair)

        native = [j for j in jobs if "native-profiling" in j.get("job_type_name", "")]
        pair = await from_jobs(native, "native")
        if pair:
            return pair
        # Speedometer3 never falls back to Gecko profiles, even when an exact
        # measurement-task pair exists. Other suites require a matched pair of
        # the same profile type; do not mix native-before with Gecko-after.
        if not speedometer and alert["repository"] == "autoland":
            pair = {
                "platform": platform,
                "suite": suite,
                "profile_kind": "gecko",
                "fallback_reason": "No downloadable native before/after pair",
                "repository": "autoland",
                **configuration,
            }
            try:
                for side, field in (
                    ("before", "prev_taskcluster_metadata"),
                    ("after", "taskcluster_metadata"),
                ):
                    metadata = row.get(field) or {}
                    task_id = metadata.get("task_id", "")
                    if not re.fullmatch(r"[A-Za-z0-9_-]{22}", task_id):
                        raise EvidenceError("Invalid profile task identity")
                    task = await self.get(f"{TASKCLUSTER}/api/queue/v1/task/{task_id}")
                    env = task.get("payload", {}).get("env", {})
                    revision = env.get("GECKO_HEAD_REV", "")
                    if (
                        task.get("metadata", {}).get("name") != measurement_task
                        or env.get("GECKO_HEAD_REPOSITORY", "").rstrip("/")
                        != f"https://hg.mozilla.org/{REPOSITORIES[repo]}"
                        or not re.fullmatch(r"[0-9a-f]{40}", revision)
                    ):
                        raise EvidenceError("Cannot verify profile task provenance")
                    pair[side] = {
                        **await self.download_profile(
                            task_id,
                            metadata.get("retry_id", 0),
                            dest,
                            **download_options,
                        ),
                        "revision": revision,
                    }
                return record_provenance(pair)
            except EvidenceError, httpx.HTTPError:
                pass

        if not speedometer:
            pair = await from_jobs(
                [
                    j
                    for j in jobs
                    if "native-profiling" not in j.get("job_type_name", "")
                ],
                "gecko",
            )
            if pair:
                pair["fallback_reason"] = "No downloadable native before/after pair"
                return pair
        raise EvidenceError(
            f"No successful before/after native profiles for {platform} ({suite})"
            if speedometer
            else f"No native or Gecko profile pair for {platform} ({suite})"
        )

    async def collect(
        self,
        bug_id: int,
        alert_id: int | None,
        directory: Path,
        suites: list[str] | None = None,
    ) -> dict:
        directory.mkdir(parents=True, exist_ok=True)
        report = await self.bug(bug_id)
        write_json(directory / "bug.json", report)
        if alert_id is None:
            ids = find_alert_ids(report["comments"])
            if not ids:
                raise EvidencePending("The bug does not link a Perfherder alert yet")
            if len(ids) != 1:
                raise EvidenceError(
                    "Expected one alert link in the bug; specify alert_id to disambiguate"
                )
            alert_id = ids[0]
        alert = await self.alert(alert_id, bug_id)
        write_json(directory / "alert.json", alert)
        # Before any history, culprit or profile download: out-of-scope alerts
        # stop here.
        candidates = regression_candidates(alert, suites)[:MAX_CANDIDATES]
        history = await self.history(alert, candidates)
        rows, tasks, excluded = await self.select(alert, history, suites)
        good_git = await self.git_revision(alert["prev_push_revision"])
        bad_git = await self.git_revision(alert["revision"])
        for culprit in report["bug"].get("regressed_by", []):
            try:
                culprit_report = await self.bug(int(culprit))
            except (EvidenceError, ToolError) as exc:
                # Security-restricted regressors are expected; record the gap.
                culprit_report = {"bug_id": int(culprit), "unavailable": str(exc)}
            write_json(directory / f"culprit-{int(culprit)}.json", culprit_report)
        hg_repo = REPOSITORIES[alert["repository"]]
        pushes = await self.get(
            f"https://hg.mozilla.org/{hg_repo}/json-pushes",
            changeset=alert["revision"],
            version=2,
        )
        commits = [
            rev for push in pushes["pushes"].values() for rev in push["changesets"]
        ]
        if not commits:
            raise EvidenceError("Culprit push has no changesets")
        culprit_push_skipped = None
        if len(commits) > MAX_CULPRIT_CHANGESETS:
            # Typically a mozilla-central merge: profiles and measurements are
            # still useful, but the push is too large to attribute.
            culprit_push_skipped = (
                f"Culprit push has {len(commits)} changesets; "
                f"patches are only collected for up to {MAX_CULPRIT_CHANGESETS}"
            )
            commits = []
        vcs = MozillaVcsContext(
            client=self.client,
            timeout=120,
            user_agent="Hackbot-Perf-Regression/0.1",
            max_diff_bytes=MAX_CULPRIT_PATCH_BYTES,
        )
        culprit_files = set()
        harness_changes_allowed = culprit_push_skipped is None
        for revision in commits:
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise EvidenceError("Invalid changeset in culprit push")
            patch = await get_commit_diff(vcs, revision, repo=hg_repo)
            text = patch["diff"]
            if patch["truncated"]:
                text += (
                    f"\n[Truncated at {MAX_CULPRIT_PATCH_BYTES} bytes; fetch "
                    f"https://hg.mozilla.org/{hg_repo}/raw-rev/{revision} for the rest]\n"
                )
            (directory / f"culprit-{revision}.patch").write_text(text)
            try:
                info = await get_commit_info(vcs, revision, repo=hg_repo)
            except (ToolError, ValueError) as exc:
                info = {"node": revision, "files": None, "unavailable": str(exc)}
            write_json(directory / f"culprit-{revision}.json", info)
            files = info.get("files")
            if not isinstance(files, list) or not files:
                harness_changes_allowed = False
                continue
            for item in files:
                path = item.get("file") if isinstance(item, dict) else item
                if not isinstance(path, str) or not path:
                    harness_changes_allowed = False
                    continue
                culprit_files.add(path)
                if path != ".taskcluster.yml" and not path.startswith(
                    HARNESS_INFRA_PATHS
                ):
                    harness_changes_allowed = False
        # The week of history is evidence for the model; validation derives its
        # good/bad baselines from the same window. No control pushes are made.
        write_json(directory / "historical-data.json", history)
        profiles = []
        seen = set()
        for row in rows:
            platform = row["series_signature"]["machine_platform"]
            profile_key = (
                row["series_signature"]["framework_id"],
                platform,
                row["series_signature"]["suite"],
                row["series_signature"].get("application", "firefox"),
                row["series_signature"].get("option_collection_hash", ""),
                tuple(sorted(row["series_signature"].get("extra_options", []))),
            )
            if profile_key in seen:
                continue
            seen.add(profile_key)
            try:
                profiles.append(await self.profile_pair(alert, row, directory))
            except (EvidenceError, httpx.HTTPError) as exc:
                profiles.append(
                    {
                        "platform": platform,
                        "suite": row["series_signature"]["suite"],
                        "unavailable": str(exc),
                    }
                )
        manifest = {
            "bug_id": bug_id,
            "alert_id": alert_id,
            "alert": alert,
            "suites": suites,
            "selection_policy": "Up to 3 regression metrics by percentage "
            "that have a CI task and enough historical jobs to validate",
            "metrics": rows,
            "metric_row_ids": [row["id"] for row in rows],
            "excluded_metrics": excluded,
            "good_git": good_git,
            "bad_git": bad_git,
            "culprit_files": sorted(culprit_files),
            "culprit_push_skipped": culprit_push_skipped,
            "harness_changes_allowed": harness_changes_allowed,
            "profiles": profiles,
            "tasks": tasks,
            "evidence_directory": str(directory),
        }
        write_json(directory / "manifest.json", manifest)
        return manifest
