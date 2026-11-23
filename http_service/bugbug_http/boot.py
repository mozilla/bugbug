# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import concurrent.futures
import contextlib
import json
import logging
import os
import re
from typing import Iterable, Iterator

import hglib
import orjson
import requests
import tenacity
import yaml
from tqdm import tqdm

from bugbug import build_platforms, repository, test_scheduling, utils
from bugbug_http import ALLOW_MISSING_MODELS, REPO_DIR

logger = logging.getLogger(__name__)

# The number of recent autoland pushes whose target tasks are the known tasks. They include backstop
# pushes, which target all tasks (one every 20 pushes or 4 hours).
AUTOLAND_PUSHES = 50
# How often the data about the current tasks, build platforms and commits is retrieved again (in
# seconds, see bugbug_http.refresh).
REFRESH_INTERVAL = 24 * 60 * 60
# How often the test tasks among which configs are selected are updated with the latest pushes (in
# seconds, see bugbug_http.refresh and update_config_selection_tasks).
CONFIG_SELECTION_TASKS_REFRESH_INTERVAL = 10 * 60
CONFIG_SELECTION_TASKS_PATH = "config_selection_tasks.json"
# The timeouts of the requests for the current tasks (to connect, and between bytes received, in
# seconds), so that a stalled request fails (and is retried) instead of blocking the boot or the
# refresh forever.
REQUEST_TIMEOUT = (30, 120)


def _write(path: str, content: str) -> None:
    """Write a file atomically, as jobs can read it at any time (see bugbug_http.refresh)."""
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w") as f:
        f.write(content)
    os.replace(tmp_path, path)


def _decision_artifact_url(rev: str, path: str) -> str:
    return f"https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.autoland.revision.{rev}.taskgraph.decision/artifacts/public/{path}"


def _without_chunk(label: str) -> str:
    return re.sub(r"-\d+$", "", label)


def _minimal_task(task: dict) -> dict:
    """The parts of a task's definition needed to compute its config and manifests."""
    extra = task["task"]["extra"]
    return {
        "label": task["label"],
        "attributes": {"test_manifests": task["attributes"]["test_manifests"]},
        "optimization": task.get("optimization"),
        "task": {
            "extra": {
                key: extra[key]
                for key in ("treeherder-platform", "suite", "test-setting")
                if key in extra
            }
        },
    }


def _eligible_tasks(
    task_graph: dict[str, dict], rev: str, failed: dict[str, list[dict]] | None = None
) -> Iterator[tuple[str, str, list[str]]]:
    """The test tasks of a task graph (of the push `rev`) that test selection optimizes: (label
    without chunk number, config as mozci names it, manifests).

    The tasks that test selection doesn't optimize (e.g. the standalone ones, which only run on
    backstop pushes) are left out. So are shippable tasks, as they are too costly to select: the
    config selection models are trained with the configs of their non-shippable equivalents (see
    test_scheduling.rename_task), which are selected instead. The tasks whose config can't be
    computed (e.g. with a test variant mozci doesn't know) are added to `failed`, by label without
    chunk number, with the parts of their definitions needed to try again (see _minimal_task): they
    may be eligible.
    """
    # mozci.data needs to be imported before mozci.task to avoid a circular import.
    import mozci.data  # noqa: F401
    from mozci.task import get_configuration

    utils.setup_mozci()

    configurations: dict[str, str | None] = {}
    for task in task_graph.values():
        manifests = task["attributes"].get("test_manifests")
        if (
            not task["label"].startswith("test-")
            or not manifests
            or "test" not in (task.get("optimization") or {})
        ):
            continue

        label = _without_chunk(task["label"])
        if label not in configurations:
            try:
                configuration = get_configuration(task, "autoland", rev)
            except Exception:
                logger.warning(
                    "Failed to get the configuration of %s",
                    task["label"],
                    exc_info=True,
                )
                configuration = None
                if failed is not None:
                    failed[label] = []
            if (
                configuration is not None
                and test_scheduling.rename_task(configuration) != configuration
            ):
                configuration = None
            configurations[label] = configuration

        config = configurations[label]
        if config is not None:
            yield label, config, manifests
        elif failed is not None and label in failed:
            failed[label].append(_minimal_task(task))


def get_config_selection_tasks(
    task_graph: dict[str, dict],
    target_tasks: Iterable[str],
    rev: str,
    failed: dict[str, list[dict]] | None = None,
) -> dict[str, dict]:
    """The test tasks among which configs are selected, by label without chunk number.

    They are the eligible test tasks (see _eligible_tasks) of `task_graph` (a backstop push's
    task-graph.json, which has all of them with all their manifests) which are among the push's
    target tasks (`target_tasks`, its target-tasks.json: the task graph also has the tasks they
    depend on), with their config and manifests (see testconfigselect.LiveTask). The labels of the
    tasks whose config can't be computed are added to `failed` (see _eligible_tasks).
    """
    targets = {_without_chunk(label) for label in target_tasks}
    tasks: dict[str, dict] = {}
    for label, config, manifests in _eligible_tasks(task_graph, rev, failed):
        if label not in targets:
            continue
        task = tasks.setdefault(label, {"config": config, "manifests": []})
        task["manifests"] = sorted(set(task["manifests"]) | set(manifests))
    return tasks


def _get(rev: str, path: str) -> requests.Response:
    return requests.get(_decision_artifact_url(rev, path), timeout=REQUEST_TIMEOUT)


def _unresolved(rev: str, backstop: bool, failed: dict[str, list[dict]]) -> dict:
    """The tasks of a push whose config couldn't be computed, to try again (see _resolve_unresolved)."""
    return {
        rev: {
            "backstop": backstop,
            "tasks": [task for tasks in failed.values() for task in tasks],
        }
    }


def _classify_new_tasks(
    state: dict, ignored: set[str], rev: str, labels: set[str], graph: dict[str, dict]
) -> dict[str, list[dict]]:
    """Classify test tasks of a push after the backstop push.

    `labels` are the tasks to classify (without chunk number), `graph` the definitions available
    for them (some can be missing, e.g. from the push's full task graph). The eligible ones become
    new tasks (unless they are candidates already), and the others are ignored, except the ones
    whose config can't be computed, which are returned (see _eligible_tasks).
    """
    failed: dict[str, list[dict]] = {}
    eligible = {
        label: config for label, config, _ in _eligible_tasks(graph, rev, failed)
    }
    for label in labels - failed.keys():
        if label not in eligible:
            ignored.add(label)
        elif label not in state["tasks"]:
            logger.info("New test task %s (%s) in push %s", label, eligible[label], rev)
            state["new_tasks"][label] = eligible[label]
    return failed


def _resolve_unresolved(state: dict, ignored: set[str], revs: set[str]) -> None:
    """Try again to compute the configs of the tasks whose config couldn't be computed before.

    The ones of the backstop push become candidates (with their manifests), the ones of the later
    pushes new tasks, and the ones which aren't eligible are ignored. Those whose push isn't recent
    anymore (other than the backstop push) are forgotten.
    """
    for rev, entry in list(state["unresolved"].items()):
        if not entry["backstop"] and rev not in revs:
            del state["unresolved"][rev]
            continue

        graph = {task["label"]: task for task in entry["tasks"]}
        labels = {_without_chunk(label) for label in graph}
        if entry["backstop"]:
            failed: dict[str, list[dict]] = {}
            tasks = get_config_selection_tasks(graph, graph, rev, failed)
            state["tasks"].update(tasks)
            ignored |= labels - tasks.keys() - failed.keys()
        else:
            failed = _classify_new_tasks(state, ignored, rev, labels, graph)

        if failed:
            entry["tasks"] = [task for tasks in failed.values() for task in tasks]
        else:
            del state["unresolved"][rev]


def update_config_selection_tasks(revs: list[str], known_tasks: set[str]) -> None:
    """Update the test tasks among which configs are selected with the latest pushes.

    The tasks come from the task graph of the latest backstop push, which runs all of them with all
    their manifests (the manifests of the test tasks of other pushes are restricted to the ones
    selected by bugbug). Older backstop pushes are used if the graph of a newer one isn't
    available or has none of the tasks.

    The tasks of the pushes after it which aren't known (e.g. because a task was renamed since the
    backstop push) are stored too, with their config: the taskgraph matches the labels of its tasks
    with the ones of the selected configs, which would otherwise not run on the renamed tasks. They
    are found among the target tasks of the pushes (before optimization, which could remove them as
    they match none of the labels in the replies), and their definitions in the full task graph
    (only when there are some). Their manifests aren't known (only the ones selected by bugbug run on
    those pushes), so the replies give them for all the groups the configs are selected for (see
    testconfigselect.task_labels). The test tasks which aren't eligible (see _eligible_tasks) are
    remembered, so that they aren't looked for again.

    `revs` are the recent autoland pushes, from the oldest. `known_tasks` are their target tasks
    (possibly from an earlier refresh), only used to forget the ignored tasks no push targets anymore.
    """
    try:
        with open(CONFIG_SELECTION_TASKS_PATH, "rb") as f:
            state = orjson.loads(f.read())
    except FileNotFoundError:
        state = None
    previous = orjson.dumps(state)

    # The latest backstop push with tasks.
    known_backstop = state["backstop"] if state is not None else None
    not_backstops = set(state["not_backstops"]) if state is not None else set()
    for rev in reversed(revs):
        if rev == known_backstop:
            break
        if rev in not_backstops:
            continue
        r = _get(rev, "parameters.yml")
        if not r.ok:
            continue
        if not yaml.safe_load(r.text).get("backstop"):
            not_backstops.add(rev)
            continue
        r = _get(rev, "task-graph.json")
        targets = _get(rev, "target-tasks.json")
        if not r.ok or not targets.ok:
            continue
        task_graph = orjson.loads(r.content)
        target_tasks = targets.json()
        failed: dict[str, list[dict]] = {}
        tasks = get_config_selection_tasks(task_graph, target_tasks, rev, failed)
        logger.info(
            "Retrieved %d test tasks from the backstop push %s", len(tasks), rev
        )
        if tasks:
            # The test tasks which aren't eligible: the ones of the task graph which aren't among
            # the tasks, and the target ones which aren't in the task graph (all the tasks that
            # test selection optimizes run on backstop pushes, so the others are optimized
            # otherwise, e.g. jittest when no JS engine file changed). The ones found before are
            # kept, as long as they are among the target tasks of the recent pushes, as some are
            # only targeted by some pushes (e.g. test-verify when tests are modified). The ones
            # whose config couldn't be computed may be eligible: they are looked for again later.
            recent_targets = {_without_chunk(label) for label in known_tasks}
            previously_ignored = set(state["ignored"]) if state is not None else set()
            ignored = (
                (
                    {
                        _without_chunk(label)
                        for label in (
                            *(task["label"] for task in task_graph.values()),
                            *target_tasks,
                        )
                        if label.startswith("test-")
                    }
                    | (previously_ignored & recent_targets)
                )
                - tasks.keys()
                - failed.keys()
            )
            state = {
                "backstop": rev,
                "tasks": tasks,
                "new_tasks": {},
                "ignored": sorted(ignored),
                "unresolved": _unresolved(rev, True, failed) if failed else {},
                "processed": [],
            }
            break

    if state is None:
        logger.warning("No backstop push with test tasks to retrieve")
        return
    state["not_backstops"] = sorted(not_backstops & set(revs))

    ignored = set(state["ignored"])
    _resolve_unresolved(state, ignored, set(revs))
    pending = {
        _without_chunk(task["label"])
        for entry in state["unresolved"].values()
        for task in entry["tasks"]
    }

    # The tasks of the later pushes. The ones without their target tasks or full task graph
    # (yet, e.g. if the decision task is still running, or ever, e.g. for DONTBUILD pushes or failed
    # decision tasks) are left for the next refresh, without blocking the others.
    later = (
        revs[revs.index(state["backstop"]) + 1 :] if state["backstop"] in revs else revs
    )
    processed = set(state["processed"])
    for rev in later:
        if rev in processed:
            continue
        r = _get(rev, "target-tasks.json")
        if not r.ok:
            continue
        unknown = {
            label
            for label in r.json()
            if label.startswith("test-")
            and _without_chunk(label) not in state["tasks"]
            and _without_chunk(label) not in state["new_tasks"]
            and _without_chunk(label) not in ignored
            and _without_chunk(label) not in pending
        }
        if unknown:
            r = _get(rev, "full-task-graph.json")
            if not r.ok:
                continue
            graph = test_scheduling.get_full_graph_tasks(r.content, unknown)
            del r
            failed = _classify_new_tasks(
                state, ignored, rev, {_without_chunk(label) for label in unknown}, graph
            )
            if failed:
                state["unresolved"].update(_unresolved(rev, False, failed))
                pending |= failed.keys()
        processed.add(rev)
    state["processed"] = sorted(processed & set(revs))
    state["ignored"] = sorted(ignored)

    serialized = orjson.dumps(state)
    if serialized != previous:
        _write(CONFIG_SELECTION_TASKS_PATH, serialized.decode())


def get_recent_revs() -> list[str]:
    """The latest AUTOLAND_PUSHES autoland pushes, from the oldest."""
    # The pushes of the last week (many more than AUTOLAND_PUSHES, also during holidays).
    r = requests.get(
        "https://hg.mozilla.org/integration/autoland/json-pushes",
        params={"version": "2", "tipsonly": "1", "startdate": "1 week ago"},
        timeout=REQUEST_TIMEOUT,
    )
    r.raise_for_status()
    pushes = sorted(r.json()["pushes"].items(), key=lambda push: int(push[0]))
    return [push_obj["changesets"][0] for _, push_obj in pushes[-AUTOLAND_PUSHES:]]


def refresh_config_selection_tasks() -> None:
    """Update the test tasks among which configs are selected (see update_config_selection_tasks)."""
    with open("known_tasks", "r") as f:
        known_tasks = {line.strip() for line in f}
    update_config_selection_tasks(get_recent_revs(), known_tasks)


@tenacity.retry(
    stop=tenacity.stop_after_attempt(7),
    wait=tenacity.wait_exponential(multiplier=1, min=1, max=8),
)
def retrieve_schedulable_tasks() -> None:
    revs = get_recent_revs()

    logger.info("Retrieving known tasks from %s...", revs)

    # Store in a file the list of tasks in the latest autoland pushes, and the test tasks among which
    # configs are selected for the manifests. Pushes don't all target the same tasks (only some of
    # them target almost all), so we use the union of the target tasks of many pushes, which also
    # protects us from broken decision tasks.
    known_tasks = set()
    for rev in revs:
        r = _get(rev, "target-tasks.json")
        if r.ok:
            known_tasks.update(r.json())

    logger.info("Retrieved %d tasks", len(known_tasks))

    assert len(known_tasks) > 0

    _write("known_tasks", "\n".join(known_tasks))

    # The test tasks among which configs are selected for the manifests.
    update_config_selection_tasks(revs, known_tasks)


def retrieve_build_platforms() -> None:
    # The files built on each platform, see build_platforms.
    built = build_platforms.retrieve_built_files()
    _write("build_platforms.json", json.dumps(built))
    logger.info("Retrieved the files built on %s.", ", ".join(sorted(built)))


def update_commits() -> None:
    """Mine the autoland commits after the last one in the commits DB, and update the DBs derived from them."""
    logger.info("Browsing all commits...")
    nodes = collections.deque(
        (commit["node"] for commit in tqdm(repository.get_commits())),
        maxlen=4096,
    )
    nodes.reverse()
    logger.info("All commits browsed.")

    with hglib.open(REPO_DIR) as hg:
        # Try using nodes backwards, in case we have some node that was on central at the time
        # we mined commits, but is not yet on autoland.
        for node in nodes:
            try:
                revs = repository.get_revs(hg, rev_start=f"children({node})")
                break
            except hglib.error.CommandError as e:
                if b"abort: unknown revision" not in e.err:
                    raise

    logger.info("Updating commits DB...")
    try:
        commits = repository.download_commits(REPO_DIR, revs=revs)
        logger.info("Commits DB updated.")

        logger.info("Updating touched together DB...")
        if len(commits) > 0:
            # Update the touched together DB.
            update_touched_together_gen = test_scheduling.update_touched_together()
            next(update_touched_together_gen)

            update_touched_together_gen.send(commits[-1]["node"])

            with contextlib.suppress(StopIteration):
                update_touched_together_gen.send(None)

        logger.info("Touched together DB updated.")
    except Exception as e:
        # It's not ideal, but better not to crash the service!
        logger.error("Exception while updating commits DB: %s", e)


def refresh_commits() -> None:
    """Pull the latest autoland commits, and add them to the commits DB (see update_commits)."""
    repository.pull(REPO_DIR, "integration/autoland", "tip")
    update_commits()


def refresh_data() -> None:
    """Retrieve the data about the current tasks, build platforms and commits again.

    If retrieving some of the data fails, the previous data is kept.
    """
    try:
        retrieve_schedulable_tasks()
    except Exception:
        logger.exception("Failed to refresh the current tasks")

    try:
        retrieve_build_platforms()
    except Exception:
        logger.exception("Failed to refresh the build platforms")

    try:
        refresh_commits()
    except Exception:
        logger.exception("Failed to refresh the commits")


def boot_worker() -> None:
    # Clone autoland
    def clone_autoland() -> None:
        logger.info("Cloning autoland in %s...", REPO_DIR)
        repository.clone(
            REPO_DIR, "https://hg.mozilla.org/integration/autoland", update=True
        )

    def extract_past_failures_label() -> None:
        try:
            utils.extract_file(
                os.path.join("data", test_scheduling.PAST_FAILURES_LABEL_DB)
            )
            logger.info("Label-level past failures DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Label-level past failures DB not extracted, but missing models are allowed."
            )

    def extract_failing_together_label() -> None:
        try:
            utils.extract_file(
                os.path.join("data", test_scheduling.FAILING_TOGETHER_LABEL_DB)
            )
            logger.info("Failing together label DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Failing together label DB not extracted, but missing models are allowed."
            )

    def extract_failing_together_config_group() -> None:
        try:
            utils.extract_file(
                os.path.join("data", test_scheduling.FAILING_TOGETHER_CONFIG_GROUP_DB)
            )
            logger.info("Failing together config/group DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Failing together config/group DB not extracted, but missing models are allowed."
            )

    def extract_past_failures_group() -> None:
        try:
            utils.extract_file(
                os.path.join("data", test_scheduling.PAST_FAILURES_GROUP_DB)
            )
            logger.info("Group-level past failures DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Group-level past failures DB not extracted, but missing models are allowed."
            )

    def extract_touched_together() -> None:
        try:
            utils.extract_file(
                os.path.join("data", test_scheduling.TOUCHED_TOGETHER_DB)
            )
            logger.info("Touched together DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Touched together DB not extracted, but missing models are allowed."
            )

    def extract_commits() -> bool:
        try:
            utils.extract_file(f"{repository.COMMITS_DB}.zst")
            logger.info("Commits DB extracted.")
            return True
        except FileNotFoundError:
            logger.info("Commits DB not extracted, but missing models are allowed.")
            assert ALLOW_MISSING_MODELS
            return False

    def extract_commit_experiences() -> None:
        try:
            utils.extract_file(os.path.join("data", repository.COMMIT_EXPERIENCES_DB))
            logger.info("Commit experiences DB extracted.")
        except FileNotFoundError:
            assert ALLOW_MISSING_MODELS
            logger.info(
                "Commit experiences DB not extracted, but missing models are allowed."
            )

    with concurrent.futures.ThreadPoolExecutor() as executor:
        clone_autoland_future = executor.submit(clone_autoland)

        retrieve_schedulable_tasks_future = executor.submit(retrieve_schedulable_tasks)

        retrieve_build_platforms_future = executor.submit(retrieve_build_platforms)

        commits_db_extracted = extract_commits()
        extract_commit_experiences()
        extract_touched_together()
        extract_past_failures_label()
        extract_past_failures_group()
        extract_failing_together_label()
        extract_failing_together_config_group()

        if commits_db_extracted:
            # Wait repository to be cloned, as it's required to call repository.download_commits.
            logger.info("Waiting autoland to be cloned...")
            clone_autoland_future.result()

            update_commits()

        # Wait list of schedulable tasks to be downloaded and written to disk.
        retrieve_schedulable_tasks_future.result()

        clone_autoland_future.result()
        retrieve_build_platforms_future.result()

    # Parse the test manifests once, before the worker forks for each job, so jobs only need to
    # parse the manifests which changed since then.
    try:
        with hglib.open(REPO_DIR) as hg:
            tip = hg.log(revrange=b"tip")[0].node.decode("ascii")
        test_scheduling.find_manifests_for_paths(REPO_DIR, [], tip)
    except Exception:
        logger.exception("Failed to parse the test manifests")

    logger.info("Worker boot done")
