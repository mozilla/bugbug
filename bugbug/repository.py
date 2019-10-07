# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import copy
import itertools
import json
import logging
import math
import os
import pickle
import sys
import threading
from datetime import datetime

import hglib
from tqdm import tqdm

from bugbug import db, utils

logger = logging.getLogger(__name__)

hg_servers = list()
hg_servers_lock = threading.Lock()
thread_local = threading.local()

COMMITS_DB = "data/commits.json"
db.register(
    COMMITS_DB,
    "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/commits.json.zst",
    3,
    ["commit_experiences.pickle.zst"],
)

path_to_component = {}

EXPERIENCE_TIMESPAN = 90
EXPERIENCE_TIMESPAN_TEXT = f"{EXPERIENCE_TIMESPAN}_days"

TYPES_TO_EXT = {
    "Javascript": [".js", ".jsm", ".sjs"],
    "C/C++": [".c", ".cpp", ".cc", ".cxx", ".m", ".mm", ".h", ".hh", ".hpp", ".hxx"],
    "Java": [".java"],
    "Python": [".py"],
    "Rust": [".rs"],
    "Kotlin": [".kt"],
    "HTML/XHTML/XUL": [".html", ".htm", ".xhtml", ".xht", ".xul"],
    "Image": [
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".icns",
        ".psd",
        ".tiff",
        ".ttf",
        ".bcmap",
        ".webp",
    ],
    "Archive": [".zip", ".gz", ".bz2", ".tar", ".xpi", ".jar"],
    "Video": [".mp4", ".webm", ".ogv", ".avi", ".mov", ".m4s", ".mgif"],
    "Audio": [".mp3", ".ogg", ".wav", ".flac", ".opus"],
    "Executable": [".exe", ".dll", ".so", ".class"],
    "Document": [".pdf", ".doc", ".otf"],
}

EXT_TO_TYPES = {ext: typ for typ, exts in TYPES_TO_EXT.items() for ext in exts}


class Commit:
    def __init__(
        self,
        node,
        author,
        desc,
        date,
        pushdate,
        bug_id,
        backedoutby,
        author_email,
        reviewers,
        ignored=False,
    ):
        self.node = node
        self.author = author
        self.bug_id = bug_id
        self.desc = desc
        self.date = date
        self.pushdate = pushdate
        self.backedoutby = backedoutby
        self.ever_backedout = backedoutby != ""
        self.author_email = author_email
        self.reviewers = reviewers
        self.ignored = ignored
        self.added = 0
        self.test_added = 0
        self.deleted = 0
        self.test_deleted = 0
        self.types = set()
        self.seniority_author = 0.0
        self.total_file_size = 0
        self.average_file_size = 0
        self.maximum_file_size = 0
        self.minimum_file_size = 0
        self.files_modified_num = 0
        self.total_test_file_size = 0
        self.average_test_file_size = 0
        self.maximum_test_file_size = 0
        self.minimum_test_file_size = 0
        self.test_files_modified_num = 0

    def __eq__(self, other):
        assert isinstance(other, Commit)
        return self.node == other.node

    def __hash__(self):
        return hash(self.node)

    def set_files(self, files, file_copies):
        self.files = files
        self.file_copies = file_copies
        self.components = list(
            set(path_to_component[path] for path in files if path in path_to_component)
        )
        self.directories = get_directories(files)
        return self

    def set_experience(
        self, exp_type, commit_type, timespan, exp_sum, exp_max, exp_min
    ):
        exp_str = f"touched_prev_{timespan}_{exp_type}_"
        if commit_type:
            exp_str += f"{commit_type}_"
        setattr(self, f"{exp_str}sum", exp_sum)
        if exp_type != "author":
            setattr(self, f"{exp_str}max", exp_max)
            setattr(self, f"{exp_str}min", exp_min)

    def to_dict(self):
        d = self.__dict__
        for f in ["backedoutby", "ignored", "file_copies"]:
            del d[f]
        d["types"] = list(d["types"])
        d["pushdate"] = str(d["pushdate"])
        d["date"] = str(d["date"])
        return d


def get_directories(files):
    if isinstance(files, str):
        files = [files]

    directories = set()
    for path in files:
        path_dirs = (
            os.path.dirname(path).split("/", 2)[:2] if os.path.dirname(path) else []
        )
        if path_dirs:
            directories.update([path_dirs[0], "/".join(path_dirs)])
    return list(directories)


def get_commits():
    return db.read(COMMITS_DB)


def _init(repo_dir):
    global HG
    os.chdir(repo_dir)
    HG = hglib.open(".")


def _init_thread():
    hg_server = hglib.open(".")
    thread_local.hg = hg_server
    with hg_servers_lock:
        hg_servers.append(hg_server)


# This code was adapted from https://github.com/mozsearch/mozsearch/blob/2e24a308bf66b4c149683bfeb4ceeea3b250009a/router/router.py#L127
def is_test(path):
    return (
        "/test/" in path
        or "/tests/" in path
        or "/mochitest/" in path
        or "/unit/" in path
        or "/gtest/" in path
        or "testing/" in path
        or "/jsapi-tests/" in path
        or "/reftests/" in path
        or "/reftest/" in path
        or "/crashtests/" in path
        or "/crashtest/" in path
        or "/gtests/" in path
        or "/googletest/" in path
    )


def hg_modified_files(hg, commit):
    template = '{join(files,"|")}\\0{join(file_copies,"|")}\\0'
    args = hglib.util.cmdbuilder(
        b"log",
        template=template,
        no_merges=True,
        rev=commit.node.encode("ascii"),
        branch="central",
    )
    x = hg.rawcommand(args)
    files_str, file_copies_str = x.split(b"\x00")[:-1]

    file_copies = {}
    for file_copy in file_copies_str.decode("utf-8").split("|"):
        if not file_copy:
            continue

        parts = file_copy.split(" (")
        copied = parts[0]
        orig = parts[1][:-1]
        file_copies[sys.intern(orig)] = sys.intern(copied)

    commit.set_files(
        [sys.intern(f) for f in files_str.decode("utf-8").split("|")], file_copies
    )


def _transform(commit):
    hg_modified_files(HG, commit)

    if commit.ignored:
        return commit

    sizes = []
    test_sizes = []

    patch = HG.export(revs=[commit.node.encode("ascii")], git=True)
    patch_data = rs_parsepatch.get_counts(patch)
    for stats in patch_data:
        path = stats["filename"]

        if stats["binary"]:
            if not is_test(path):
                commit.types.add("binary")
            continue

        size = None
        if not stats["deleted"]:
            try:
                after = HG.cat([path.encode("utf-8")], rev=commit.node.encode("ascii"))
                size = after.count(b"\n")
            except hglib.error.CommandError as e:
                if b"no such file in rev" not in e.err:
                    raise

        if is_test(path):
            commit.test_files_modified_num += 1

            commit.test_added += stats["added_lines"]
            commit.test_deleted += stats["deleted_lines"]

            if size is not None:
                test_sizes.append(size)
            # We don't have a 'test' equivalent of types, as most tests are JS,
            # so this wouldn't add useful information.
        else:
            commit.files_modified_num += 1

            commit.added += stats["added_lines"]
            commit.deleted += stats["deleted_lines"]

            if size is not None:
                sizes.append(size)

            ext = os.path.splitext(path)[1].lower()
            type_ = EXT_TO_TYPES.get(ext, ext)

            commit.types.add(type_)

    commit.total_file_size = sum(sizes)
    commit.average_file_size = (
        commit.total_file_size / len(sizes) if len(sizes) > 0 else 0
    )
    commit.maximum_file_size = max(sizes, default=0)
    commit.minimum_file_size = min(sizes, default=0)

    commit.total_test_file_size = sum(test_sizes)
    commit.average_test_file_size = (
        commit.total_test_file_size / len(test_sizes) if len(test_sizes) > 0 else 0
    )
    commit.maximum_test_file_size = max(test_sizes, default=0)
    commit.minimum_test_file_size = min(test_sizes, default=0)

    return commit


def hg_log(hg, revs):
    template = "{node}\\0{author}\\0{desc}\\0{date|hgdate}\\0{bug}\\0{backedoutby}\\0{author|email}\\0{pushdate|hgdate}\\0{reviewers}\\0"

    args = hglib.util.cmdbuilder(
        b"log",
        template=template,
        no_merges=True,
        rev=revs[0] + b":" + revs[-1],
        branch="central",
    )
    x = hg.rawcommand(args)
    out = x.split(b"\x00")[:-1]

    revs = []
    for rev in hglib.util.grouper(template.count("\\0"), out):
        assert b" " in rev[3]
        date = datetime.utcfromtimestamp(float(rev[3].split(b" ", 1)[0]))

        assert b" " in rev[7]
        pushdate_timestamp = rev[7].split(b" ", 1)[0]
        if pushdate_timestamp != b"0":
            pushdate = datetime.utcfromtimestamp(float(pushdate_timestamp))
        else:
            pushdate = datetime.utcnow()

        bug_id = int(rev[4].decode("ascii")) if rev[4] else None

        reviewers = (
            set(sys.intern(r) for r in rev[8].decode("utf-8").split(" "))
            if rev[8] != b""
            else set()
        )

        revs.append(
            Commit(
                node=sys.intern(rev[0].decode("ascii")),
                author=sys.intern(rev[1].decode("utf-8")),
                desc=rev[2].decode("utf-8"),
                date=date,
                pushdate=pushdate,
                bug_id=bug_id,
                backedoutby=rev[5].decode("ascii"),
                author_email=rev[6].decode("utf-8"),
                reviewers=tuple(reviewers),
            )
        )

    return revs


def _hg_log(revs):
    return hg_log(thread_local.hg, revs)


def get_revs(hg, rev_start=0, rev_end="tip"):
    print(f"Getting revs from {rev_start} to {rev_end}...")

    args = hglib.util.cmdbuilder(
        b"log",
        template="{node}\n",
        no_merges=True,
        branch="central",
        rev=f"{rev_start}:{rev_end}",
    )
    x = hg.rawcommand(args)
    return x.splitlines()


def calculate_experiences(commits, first_pushdate, save=True):
    print(f"Analyzing experiences from {len(commits)} commits...")

    try:
        with open("data/commit_experiences.pickle", "rb") as f:
            experiences, first_commit_time = pickle.load(f)
    except FileNotFoundError:
        experiences = {}
        first_commit_time = {}

    for commit in tqdm(commits):
        if commit.author not in first_commit_time:
            first_commit_time[commit.author] = commit.pushdate
            commit.seniority_author = 0
        else:
            time_lapse = commit.pushdate - first_commit_time[commit.author]
            commit.seniority_author = time_lapse.total_seconds()

    # Note: In the case of files, directories, components, we can't just use the sum of previous commits, as we could end
    # up overcounting them. For example, consider a commit A which modifies "dir1" and "dir2", a commit B which modifies
    # "dir1" and a commit C which modifies "dir1" and "dir2". The number of previous commits touching the same directories
    # for C should be 2 (A + B), and not 3 (A twice + B).

    def get_experience(exp_type, commit_type, item, day, default):
        if exp_type not in experiences:
            experiences[exp_type] = {}

        if commit_type not in experiences[exp_type]:
            experiences[exp_type][commit_type] = {}

        if item not in experiences[exp_type][commit_type]:
            experiences[exp_type][commit_type][item] = utils.ExpQueue(
                day, EXPERIENCE_TIMESPAN + 1, default
            )

        return experiences[exp_type][commit_type][item][day]

    def update_experiences(experience_type, day, items):
        for commit_type in ["", "backout"]:
            total_exps = [
                get_experience(experience_type, commit_type, item, day, 0)
                for item in items
            ]
            timespan_exps = [
                exp
                - get_experience(
                    experience_type, commit_type, item, day - EXPERIENCE_TIMESPAN, 0
                )
                for exp, item in zip(total_exps, items)
            ]

            total_exps_sum = sum(total_exps)
            timespan_exps_sum = sum(timespan_exps)

            commit.set_experience(
                experience_type,
                commit_type,
                "total",
                total_exps_sum,
                max(total_exps, default=0),
                min(total_exps, default=0),
            )
            commit.set_experience(
                experience_type,
                commit_type,
                EXPERIENCE_TIMESPAN_TEXT,
                timespan_exps_sum,
                max(timespan_exps, default=0),
                min(timespan_exps, default=0),
            )

            # We don't want to consider backed out commits when calculating normal experiences.
            if (
                commit_type == ""
                and not commit.ever_backedout
                or commit_type == "backout"
                and commit.ever_backedout
            ):
                for i, item in enumerate(items):
                    experiences[experience_type][commit_type][item][day] = (
                        total_exps[i] + 1
                    )

    def update_complex_experiences(experience_type, day, items):
        for commit_type in ["", "backout"]:
            all_commit_lists = [
                get_experience(experience_type, commit_type, item, day, tuple())
                for item in items
            ]
            before_commit_lists = [
                get_experience(
                    experience_type,
                    commit_type,
                    item,
                    day - EXPERIENCE_TIMESPAN,
                    tuple(),
                )
                for item in items
            ]
            timespan_commit_lists = [
                commit_list[len(before_commit_list) :]
                for commit_list, before_commit_list in zip(
                    all_commit_lists, before_commit_lists
                )
            ]

            all_commits = set(sum(all_commit_lists, tuple()))
            timespan_commits = set(sum(timespan_commit_lists, tuple()))

            commit.set_experience(
                experience_type,
                commit_type,
                "total",
                len(all_commits),
                max(
                    (len(all_commit_list) for all_commit_list in all_commit_lists),
                    default=0,
                ),
                min(
                    (len(all_commit_list) for all_commit_list in all_commit_lists),
                    default=0,
                ),
            )
            commit.set_experience(
                experience_type,
                commit_type,
                EXPERIENCE_TIMESPAN_TEXT,
                len(timespan_commits),
                max(
                    (
                        len(timespan_commit_list)
                        for timespan_commit_list in timespan_commit_lists
                    ),
                    default=0,
                ),
                min(
                    (
                        len(timespan_commit_list)
                        for timespan_commit_list in timespan_commit_lists
                    ),
                    default=0,
                ),
            )

            # We don't want to consider backed out commits when calculating normal experiences.
            if (
                commit_type == ""
                and not commit.ever_backedout
                or commit_type == "backout"
                and commit.ever_backedout
            ):
                for i, item in enumerate(items):
                    experiences[experience_type][commit_type][item][
                        day
                    ] = all_commit_lists[i] + (commit.node,)

    for i, commit in enumerate(tqdm(commits)):
        day = (commit.pushdate - first_pushdate).days
        assert day >= 0

        # When a file is moved/copied, copy original experience values to the copied path.
        if "file" in experiences:
            xp_file = experiences["file"]
            for orig, copied in commit.file_copies.items():
                for commit_type in ["", "backout"]:
                    if orig in xp_file[commit_type]:
                        xp_file[commit_type][copied] = copy.deepcopy(
                            xp_file[commit_type][orig]
                        )
                    else:
                        print(
                            f"Experience missing for file {orig}, type '{commit_type}', on commit {commit.node}"
                        )

        if not commit.ignored:
            update_experiences("author", day, [commit.author])
            update_experiences("reviewer", day, commit.reviewers)

            update_complex_experiences("file", day, commit.files)
            update_complex_experiences("directory", day, commit.directories)
            update_complex_experiences("component", day, commit.components)

    if save:
        with open("data/commit_experiences.pickle", "wb") as f:
            pickle.dump(
                (experiences, first_commit_time), f, protocol=pickle.HIGHEST_PROTOCOL
            )


def set_commits_to_ignore(repo_dir, commits):
    # Skip commits which are in .hg-annotate-ignore-revs or which have
    # 'ignore-this-changeset' in their description (mostly consisting of very
    # large and not meaningful formatting changes).
    with open(os.path.join(repo_dir, ".hg-annotate-ignore-revs"), "r") as f:
        ignore_revs = set(l[:40] for l in f)

    backouts = set(commit.backedoutby for commit in commits if commit.ever_backedout)

    def should_ignore(commit):
        if commit.node in ignore_revs or "ignore-this-changeset" in commit.desc:
            return True

        # Don't analyze backouts.
        if commit.node in backouts:
            return True

        # Don't analyze commits that are not linked to a bug.
        if commit.bug_id is None:
            return True

        return False

    for commit in commits:
        commit.ignored = should_ignore(commit)


def download_component_mapping():
    global path_to_component

    utils.download_check_etag(
        "https://index.taskcluster.net/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        "data/component_mapping.json",
    )

    with open("data/component_mapping.json", "r") as f:
        path_to_component = json.load(f)

    path_to_component = {
        path: "::".join(component) for path, component in path_to_component.items()
    }


def hg_log_multi(repo_dir, revs):
    if len(revs) == 0:
        return []

    cwd = os.getcwd()
    os.chdir(repo_dir)

    threads_num = os.cpu_count() + 1
    REVS_COUNT = len(revs)
    CHUNK_SIZE = int(math.ceil(REVS_COUNT / threads_num))
    revs_groups = [
        (revs[i], revs[min(i + CHUNK_SIZE, REVS_COUNT) - 1])
        for i in range(0, REVS_COUNT, CHUNK_SIZE)
    ]

    with concurrent.futures.ThreadPoolExecutor(
        initializer=_init_thread, max_workers=threads_num
    ) as executor:
        commits = executor.map(_hg_log, revs_groups)
        commits = tqdm(commits, total=len(revs_groups))
        commits = list(itertools.chain.from_iterable(commits))

    os.chdir(cwd)

    while len(hg_servers) > 0:
        hg_server = hg_servers.pop()
        hg_server.close()

    return commits


def download_commits(repo_dir, rev_start=0, save=True):
    with hglib.open(repo_dir) as hg:
        revs = get_revs(hg, rev_start)
        if len(revs) == 0:
            print("No commits to analyze")
            return []

        first_pushdate = hg_log(hg, [b"0"])[0].pushdate

    print(f"Mining {len(revs)} commits using {os.cpu_count()} processes...")

    commits = hg_log_multi(repo_dir, revs)

    print("Downloading file->component mapping...")

    download_component_mapping()

    set_commits_to_ignore(repo_dir, commits)

    commits_num = len(commits)

    print(f"Mining {commits_num} commits using {os.cpu_count()} processes...")

    global rs_parsepatch
    import rs_parsepatch

    with concurrent.futures.ProcessPoolExecutor(
        initializer=_init, initargs=(repo_dir,)
    ) as executor:
        commits = executor.map(_transform, commits, chunksize=64)
        commits = tqdm(commits, total=commits_num)
        commits = list(commits)

    calculate_experiences(commits, first_pushdate, save)

    commits = [commit.to_dict() for commit in commits if not commit.ignored]

    if save:
        db.append(COMMITS_DB, commits)

    return commits


def clean(repo_dir):
    with hglib.open(repo_dir) as hg:
        hg.revert(repo_dir.encode("utf-8"), all=True)

        try:
            cmd = hglib.util.cmdbuilder(
                b"strip", rev=b"roots(outgoing())", force=True, backup=False
            )
            hg.rawcommand(cmd)
        except hglib.error.CommandError as e:
            if b"abort: empty revision set" not in e.err:
                raise

        # Pull and update.
        logger.info("Pulling and updating mozilla-central")
        hg.pull(update=True)
        logger.info("mozilla-central pulled and updated")


def clone(repo_dir):
    if os.path.exists(repo_dir):
        clean(repo_dir)
        return

    cmd = hglib.util.cmdbuilder(
        "robustcheckout",
        "https://hg.mozilla.org/mozilla-central",
        repo_dir,
        purge=True,
        sharebase=repo_dir + "-shared",
        networkattempts=7,
        branch=b"tip",
    )

    cmd.insert(0, hglib.HGPATH)

    proc = hglib.util.popen(cmd)
    out, err = proc.communicate()
    if proc.returncode:
        raise hglib.error.CommandError(cmd, proc.returncode, out, err)

    logger.info("mozilla-central cloned")

    # Remove pushlog DB to make sure it's regenerated.
    try:
        os.remove(os.path.join(repo_dir, ".hg", "pushlog2.db"))
    except FileNotFoundError:
        logger.info("pushlog database doesn't exist")

    # Pull and update, to make sure the pushlog is generated.
    clean(repo_dir)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repository_dir", help="Path to the repository", action="store")
    parser.add_argument(
        "rev_start", help="Which revision to start with", action="store"
    )
    args = parser.parse_args()

    download_commits(args.repository_dir, args.rev_start)
