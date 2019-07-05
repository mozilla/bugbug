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
import multiprocessing
import os
import pickle
import re
import sys
from collections import deque
from datetime import datetime

import hglib
from tqdm import tqdm

from bugbug import db, utils

logger = logging.getLogger(__name__)


COMMITS_DB = "data/commits.json"
db.register(
    COMMITS_DB,
    "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/commits.json.zst",
    1,
    ["commit_experiences.pickle.zst"],
)

path_to_component = {}

EXPERIENCE_TIMESPAN = 90
EXPERIENCE_TIMESPAN_TEXT = f"{EXPERIENCE_TIMESPAN}_days"


class Commit:
    def __init__(
        self,
        node,
        author,
        desc,
        date,
        pushdate,
        bug,
        backedoutby,
        author_email,
        files,
        file_copies,
        reviewers,
    ):
        self.node = node
        self.author = author
        self.desc = desc
        self.date = date
        self.pushdate = pushdate
        self.bug = bug
        self.backedoutby = backedoutby
        self.author_email = author_email
        self.files = files
        self.file_copies = file_copies
        self.reviewers = reviewers

    def __eq__(self, other):
        assert isinstance(other, Commit)
        return self.node == other.node

    def __hash__(self):
        return hash(self.node)

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


# This is only a temporary hack: Should be removed after the template issue with reviewers (https://bugzilla.mozilla.org/show_bug.cgi?id=1528938)
# gets fixed. Most of this code is copied from https://github.com/mozilla/version-control-tools/blob/2c2812d4a41b690203672a183b1dd85ca8b39e01/pylib/mozautomation/mozautomation/commitparser.py#L129
def get_reviewers(commit_description, flag_re=None):
    SPECIFIER = r"(?:r|a|sr|rs|ui-r)[=?]"
    LIST = r"[;,\/\\]\s*"
    LIST_RE = re.compile(LIST)

    IRC_NICK = r"[a-zA-Z0-9\-\_]+"
    REVIEWERS_RE = re.compile(
        r"([\s\(\.\[;,])"
        + r"("
        + SPECIFIER
        + r")"
        + r"("
        + IRC_NICK
        + r"(?:"
        + LIST
        + r"(?![a-z0-9\.\-]+[=?])"
        + IRC_NICK
        + r")*"
        + r")?"
    )

    if commit_description == "":
        return

    commit_summary = commit_description.splitlines().pop(0)
    res = []
    for match in re.finditer(REVIEWERS_RE, commit_summary):
        if not match.group(3):
            continue

        for reviewer in re.split(LIST_RE, match.group(3)):
            if flag_re is None:
                res.append(reviewer)
            elif flag_re.match(match.group(2)):
                res.append(reviewer)

    return res


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


def _transform(commit):
    obj = {
        "node": commit.node,
        "author": commit.author,
        "reviewers": commit.reviewers,
        "desc": commit.desc,
        "date": str(commit.date),
        "pushdate": str(commit.pushdate),
        "bug_id": int(commit.bug.decode("ascii")) if commit.bug else None,
        "ever_backedout": commit.backedoutby != "",
        "added": 0,
        "test_added": 0,
        "deleted": 0,
        "test_deleted": 0,
        "types": set(),
        "author_email": commit.author_email.decode("utf-8"),
    }

    # Copy all experience fields.
    for attr, value in commit.__dict__.items():
        if attr.startswith(f"touched_prev"):
            obj[attr] = value

    obj["seniority_author"] = commit.seniority_author

    sizes = []

    patch = HG.export(revs=[commit.node.encode("ascii")], git=True)
    patch_data = rs_parsepatch.get_counts(patch)
    for stats in patch_data:
        if stats["binary"]:
            obj["types"].add("binary")
            continue

        path = stats["filename"]

        if is_test(path):
            obj["test_added"] += stats["added_lines"]
            obj["test_deleted"] += stats["deleted_lines"]
        else:
            obj["added"] += stats["added_lines"]
            obj["deleted"] += stats["deleted_lines"]

        ext = os.path.splitext(path)[1]
        if ext in [".js", ".jsm"]:
            type_ = "JavaScript"
        elif ext in [
            ".c",
            ".cpp",
            ".cc",
            ".cxx",
            ".m",
            ".mm",
            ".h",
            ".hh",
            ".hpp",
            ".hxx",
        ]:
            type_ = "C/C++"
        elif ext == ".java":
            type_ = "Java"
        elif ext == ".py":
            type_ = "Python"
        elif ext == ".rs":
            type_ = "Rust"
        else:
            type_ = ext
        obj["types"].add(type_)

        if not stats["deleted"]:
            try:
                after = HG.cat([path.encode("utf-8")], rev=commit.node.encode("ascii"))
                sizes.append(after.count(b"\n"))
            except hglib.error.CommandError as e:
                if b"no such file in rev" not in e.err:
                    raise

    obj["total_file_size"] = sum(sizes)
    obj["average_file_size"] = (
        obj["total_file_size"] / len(sizes) if len(sizes) > 0 else 0
    )
    obj["maximum_file_size"] = max(sizes, default=0)
    obj["minimum_file_size"] = min(sizes, default=0)

    obj["files_modified_num"] = len(patch_data)

    # Covert to a list, as a set is not JSON-serializable.
    obj["types"] = list(obj["types"])

    obj["components"] = list(
        set(
            path_to_component[path]
            for path in commit.files
            if path in path_to_component
        )
    )
    obj["directories"] = get_directories(commit.files)
    obj["files"] = commit.files

    return obj


def hg_log(hg, revs):
    template = '{node}\\0{author}\\0{desc}\\0{date|hgdate}\\0{bug}\\0{backedoutby}\\0{author|email}\\0{join(files,"|")}\\0{join(file_copies,"|")}\\0{pushdate|hgdate}\\0'

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

        assert b" " in rev[9]
        pushdate_timestamp = rev[9].split(b" ", 1)[0]
        if pushdate_timestamp != b"0":
            pushdate = datetime.utcfromtimestamp(float(pushdate_timestamp))
        else:
            pushdate = datetime.utcnow()

        file_copies = {}
        for file_copy in rev[8].decode("utf-8").split("|"):
            if not file_copy:
                continue

            parts = file_copy.split(" (")
            copied = parts[0]
            orig = parts[1][:-1]
            file_copies[sys.intern(orig)] = sys.intern(copied)

        revs.append(
            Commit(
                node=sys.intern(rev[0].decode("ascii")),
                author=sys.intern(rev[1].decode("utf-8")),
                desc=rev[2].decode("utf-8"),
                date=date,
                pushdate=pushdate,
                bug=rev[4],
                backedoutby=rev[5].decode("ascii"),
                author_email=rev[6],
                files=[sys.intern(f) for f in rev[7].decode("utf-8").split("|")],
                file_copies=file_copies,
                reviewers=tuple(
                    set(sys.intern(r) for r in get_reviewers(rev[2].decode("utf-8")))
                ),
            )
        )

    return revs


def _hg_log(revs):
    return hg_log(HG, revs)


def get_revs(hg, rev_start=0):
    print(f"Getting revs from {rev_start} to tip...")

    args = hglib.util.cmdbuilder(
        b"log",
        template="{node}\n",
        no_merges=True,
        branch="central",
        rev=f"{rev_start}:tip",
    )
    x = hg.rawcommand(args)
    return x.splitlines()


class exp_queue:
    def __init__(self, start_day, maxlen, default):
        self.list = deque([default] * maxlen, maxlen=maxlen)
        self.start_day = start_day - (maxlen - 1)
        self.default = default

    def __deepcopy__(self, memo):
        result = exp_queue.__new__(exp_queue)

        # We don't need to deepcopy the list, as elements in the list are immutable.
        result.list = self.list.copy()
        result.start_day = self.start_day
        result.default = self.default

        return result

    @property
    def last_day(self):
        return self.start_day + (self.list.maxlen - 1)

    def __getitem__(self, day):
        assert (
            day >= self.start_day
        ), f"Can't get a day ({day}) from earlier than start day ({self.start_day})"

        if day < 0:
            return self.default

        if day > self.last_day:
            return self.list[-1]

        return self.list[day - self.start_day]

    def __setitem__(self, day, value):
        if day == self.last_day:
            self.list[day - self.start_day] = value
        elif day > self.last_day:
            last_val = self.list[-1]
            # We need to extend the list except for 2 elements (the last, which
            # is going to be the same, and the one we are adding now).
            range_end = min(day - self.last_day, self.list.maxlen) - 2
            if range_end > 0:
                self.list.extend(last_val for _ in range(range_end))

            self.start_day = day - (self.list.maxlen - 1)

            self.list.append(value)
        else:
            assert False, "Can't insert in the past"

        assert day == self.last_day


def calculate_experiences(commits, commits_to_ignore, first_pushdate, save=True):
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
            experiences[exp_type][commit_type][item] = exp_queue(
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
                and not commit.backedoutby
                or commit_type == "backout"
                and commit.backedoutby
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
                and not commit.backedoutby
                or commit_type == "backout"
                and commit.backedoutby
            ):
                for i, item in enumerate(items):
                    experiences[experience_type][commit_type][item][
                        day
                    ] = all_commit_lists[i] + (commit.node,)

    for i, commit in enumerate(tqdm(commits)):
        day = (commit.pushdate - first_pushdate).days
        assert day >= 0

        # When a file is moved/copied, copy original experience values to the copied path.
        if len(commit.file_copies) > 0:
            for orig, copied in commit.file_copies.items():
                orig_directories = get_directories(orig)
                copied_directories = get_directories(copied)

                for commit_type in ["", "backout"]:
                    for orig_directory, copied_directory in zip(
                        orig_directories, copied_directories
                    ):
                        if orig_directory in experiences["directory"][commit_type]:
                            experiences["directory"][commit_type][
                                copied_directory
                            ] = copy.deepcopy(
                                experiences["directory"][commit_type][orig_directory]
                            )
                        else:
                            print(
                                f"Experience missing for directory {orig_directory}, type '{commit_type}', on commit {commit.node}"
                            )

                    if orig in path_to_component and copied in path_to_component:
                        orig_component = path_to_component[orig]
                        copied_component = path_to_component[copied]
                        if orig_component in experiences["component"][commit_type]:
                            experiences["component"][commit_type][
                                copied_component
                            ] = copy.deepcopy(
                                experiences["component"][commit_type][orig_component]
                            )
                        else:
                            print(
                                f"Experience missing for component {orig_component}, type '{commit_type}', on commit {commit.node}"
                            )

                    if orig in experiences["file"][commit_type]:
                        experiences["file"][commit_type][copied] = copy.deepcopy(
                            experiences["file"][commit_type][orig]
                        )
                    else:
                        print(
                            f"Experience missing for file {orig}, type '{commit_type}', on commit {commit.node}"
                        )

        if commit not in commits_to_ignore:
            update_experiences("author", day, [commit.author])
            update_experiences("reviewer", day, commit.reviewers)

            update_complex_experiences("file", day, commit.files)

            update_complex_experiences("directory", day, get_directories(commit.files))

            components = list(
                set(
                    path_to_component[path]
                    for path in commit.files
                    if path in path_to_component
                )
            )

            update_complex_experiences("component", day, components)

    if save:
        with open("data/commit_experiences.pickle", "wb") as f:
            pickle.dump(
                (experiences, first_commit_time), f, protocol=pickle.HIGHEST_PROTOCOL
            )


def get_commits_to_ignore(repo_dir, commits):
    # Skip commits which are in .hg-annotate-ignore-revs or which have
    # 'ignore-this-changeset' in their description (mostly consisting of very
    # large and not meaningful formatting changes).
    with open(os.path.join(repo_dir, ".hg-annotate-ignore-revs"), "r") as f:
        ignore_revs = set(l[:40] for l in f)

    backouts = set(commit.backedoutby for commit in commits if commit.backedoutby != "")

    def should_ignore(commit):
        if commit.node in ignore_revs or "ignore-this-changeset" in commit.desc:
            return True

        # Don't analyze backouts.
        if commit.node in backouts:
            return True

        # Don't analyze commits that are not linked to a bug.
        if commit.bug == b"":
            return True

        return False

    return set(commit for commit in commits if should_ignore(commit))


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


def download_commits(repo_dir, rev_start=0, ret=False, save=True):
    hg = hglib.open(repo_dir)

    revs = get_revs(hg, rev_start)
    if len(revs) == 0:
        print("No commits to analyze")
        return []

    first_pushdate = hg_log(hg, [b"0"])[0].pushdate

    hg.close()

    processes = multiprocessing.cpu_count()

    print(f"Mining {len(revs)} commits using {processes} processes...")

    CHUNK_SIZE = 256
    revs_groups = [revs[i : (i + CHUNK_SIZE)] for i in range(0, len(revs), CHUNK_SIZE)]

    with concurrent.futures.ProcessPoolExecutor(
        initializer=_init, initargs=(repo_dir,)
    ) as executor:
        commits = executor.map(_hg_log, revs_groups, chunksize=20)
        commits = tqdm(commits, total=len(revs_groups))
        commits = list(itertools.chain.from_iterable(commits))

    print("Downloading file->component mapping...")

    download_component_mapping()

    commits_to_ignore = get_commits_to_ignore(repo_dir, commits)
    print(f"{len(commits_to_ignore)} commits to ignore")

    calculate_experiences(commits, commits_to_ignore, first_pushdate, save)

    # Exclude commits to ignore.
    commits = [commit for commit in commits if commit not in commits_to_ignore]

    commits_num = len(commits)

    print(f"Mining {commits_num} commits using {processes} processes...")

    global rs_parsepatch
    import rs_parsepatch

    with concurrent.futures.ProcessPoolExecutor(
        initializer=_init, initargs=(repo_dir,)
    ) as executor:
        commits = executor.map(_transform, commits, chunksize=64)
        commits = tqdm(commits, total=commits_num)

        if ret:
            commits = list(commits)

        if save:
            db.append(COMMITS_DB, commits)

        if ret:
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
