# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import copy
import itertools
import math
import multiprocessing
import os
import re
import sys
from collections import deque
from datetime import datetime

import hglib
import requests
from dateutil.relativedelta import relativedelta
from tqdm import tqdm

from bugbug import db

COMMITS_DB = "data/commits.json"
db.register(
    COMMITS_DB,
    "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/commits.json.xz",
)

path_to_component = {}

EXPERIENCE_TIMESPAN = 90
EXPERIENCE_TIMESPAN_TEXT = f"{EXPERIENCE_TIMESPAN}_days"

FILE_TYPES = {
    ".js": "JavaScript",
    ".jsm": "JavaScript",
    ".c": "C/C++",
    ".cpp": "C/C++",
    ".cc": "C/C++",
    ".cxx": "C/C++",
    ".m": "C/C++",
    ".mm": "C/C++",
    ".h": "C/C++",
    ".hh": "C/C++",
    ".hpp": "C/C++",
    ".hxx": "C/C++",
    ".java": "Java",
    ".py": "Python",
    ".rs": "Rust",
}


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
    ):
        self.node = node
        self.author = author
        self.desc = desc
        self.date = date
        self.pushdate = pushdate
        self.bug_id = bug_id
        self.backedoutby = backedoutby
        self.ever_backedout = backedoutby != ""
        self.author_email = author_email
        self.files = []
        self.file_copies = {}
        self.reviewers = reviewers
        self.added = 0
        self.test_added = 0
        self.deleted = 0
        self.test_deleted = 0
        self.types = set()
        self.seniority_author = 0
        self.total_file_size = 0
        self.average_file_size = 0
        self.maximum_file_size = 0
        self.minimum_file_size = 0
        self.files_modified_num = 0
        self.components = []
        self.directories = []
        self.touched_prev = set()

    def to_dict(self):
        data = {
            "node": self.node,
            "author": self.author,
            "desc": self.desc,
            "date": str(self.date),
            "pushdate": str(self.pushdate),
            "bug_id": self.bug_id,
            "ever_backedout": self.ever_backedout,
            "author_email": self.author_email,
            "files": self.files,
            "reviewers": self.reviewers,
            "added": self.added,
            "test_added": self.test_added,
            "deleted": self.deleted,
            "test_deleted": self.test_deleted,
            "types": list(self.types),
            "seniority_author": self.seniority_author,
            "total_file_size": self.total_file_size,
            "average_file_size": self.average_file_size,
            "maximum_file_size": self.maximum_file_size,
            "minimum_file_size": self.minimum_file_size,
            "files_modified_num": self.files_modified_num,
            "components": self.components,
            "directories": self.directories,
        }
        for attr in self.touched_prev:
            data[attr] = getattr(self, attr)
        return data

    def set_attr(self, attr, val):
        setattr(self, attr, val)
        self.touched_prev.add(attr)

    def set_experience(self, exp_type, timespan, exp_sum, exp_max, exp_min):
        exp_str = f"touched_prev_{timespan}_{exp_type}_"
        self.set_attr(f"{exp_str}sum", exp_sum)
        if exp_type != "author":
            self.set_attr(f"{exp_str}max", exp_max)
            self.set_attr(f"{exp_str}min", exp_min)

    def set_files(self, files):
        self.files = files
        self.components = list(
            set(path_to_component[path] for path in files if path in path_to_component)
        )
        self.directories = get_directories(files)


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
    hg_modified_files(HG, commit)

    sizes = []

    patch = HG.export(revs=[commit.node.encode("ascii")], git=True)
    patch_data = rs_parsepatch.get_counts(patch)
    for stats in patch_data:
        if stats["binary"]:
            commit.types.add("binary")
            continue

        path = stats["filename"]

        if is_test(path):
            commit.test_added += stats["added_lines"]
            commit.test_deleted += stats["deleted_lines"]
        else:
            commit.added += stats["added_lines"]
            commit.deleted += stats["deleted_lines"]

        ext = os.path.splitext(path)[1]
        commit.types.add(FILE_TYPES.get(ext, ext))

        if not stats["deleted"]:
            try:
                after = HG.cat([path.encode("utf-8")], rev=commit.node.encode("ascii"))
                sizes.append(after.count(b"\n"))
            except hglib.error.CommandError as e:
                if b"no such file in rev" not in e.err:
                    raise

    commit.total_file_size = sum(sizes)
    commit.average_file_size = (
        commit.total_file_size / len(sizes) if len(sizes) > 0 else 0
    )
    commit.maximum_file_size = max(sizes, default=0)
    commit.minimum_file_size = min(sizes, default=0)

    commit.files_modified_num = len(patch_data)

    return commit


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

    commit.file_copies = file_copies = {}
    for file_copy in file_copies_str.decode("utf-8").split("|"):
        if not file_copy:
            continue

        parts = file_copy.split(" (")
        copied = parts[0]
        orig = parts[1][:-1]
        file_copies[sys.intern(orig)] = sys.intern(copied)

    commit.set_files([sys.intern(f) for f in files_str.decode("utf-8").split("|")])


def hg_log(hg, rev_start, rev_end):
    template = "{node}\\0{author}\\0{desc}\\0{date}\\0{bug}\\0{backedoutby}\\0{author|email}\\0{pushdate}\\0"

    args = hglib.util.cmdbuilder(
        b"log",
        template=template,
        no_merges=True,
        rev=rev_start + b":" + rev_end,
        branch="central",
    )
    x = hg.rawcommand(args)
    out = x.split(b"\x00")[:-1]

    revs = []
    for rev in hglib.util.grouper(template.count("\\0"), out):
        date = datetime.utcfromtimestamp(float(rev[3].split(b".", 1)[0]))
        pushdate = datetime.utcfromtimestamp(float(rev[7].split(b"-", 1)[0]))

        revs.append(
            Commit(
                node=sys.intern(rev[0].decode("ascii")),
                author=sys.intern(rev[1].decode("utf-8")),
                desc=rev[2].decode("utf-8"),
                date=date,
                pushdate=pushdate,
                bug_id=int(rev[4].decode("ascii")) if rev[4] else None,
                backedoutby=rev[5].decode("ascii"),
                author_email=rev[6].decode("utf-8"),
                reviewers=tuple(
                    sys.intern(r) for r in get_reviewers(rev[2].decode("utf-8"))
                ),
            )
        )

    return revs


def _hg_log(revs):
    return hg_log(HG, *revs)


def get_revs(hg):
    print(f"Getting revs from 0 to tip...")

    args = hglib.util.cmdbuilder(
        b"log", template="{node}\n", no_merges=True, branch="central", rev=f"0:tip"
    )
    x = hg.rawcommand(args)
    return x.splitlines()


class exp_queue:
    def __init__(self, start_day, maxlen, default):
        self.list = deque([default] * maxlen, maxlen=maxlen)
        self.start_day = start_day - (maxlen - 1)
        self.default = default

    @property
    def last_day(self):
        return self.start_day + (self.list.maxlen - 1)

    def __getitem__(self, day):
        assert day >= self.start_day, "Can't get a day from earlier than start day"

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


def calculate_experiences(commits):
    print(f"Analyzing experiences from {len(commits)} commits...")

    first_commit_time = {}

    for commit in tqdm(commits):
        if commit.author not in first_commit_time:
            first_commit_time[commit.author] = commit.pushdate
        else:
            time_lapse = commit.pushdate - first_commit_time[commit.author]
            commit.seniority_author = time_lapse.days

    first_pushdate = commits[0].pushdate

    # Note: In the case of files, directories, components, we can't just use the sum of previous commits, as we could end
    # up overcounting them. For example, consider a commit A which modifies "dir1" and "dir2", a commit B which modifies
    # "dir1" and a commit C which modifies "dir1" and "dir2". The number of previous commits touching the same directories
    # for C should be 2 (A + B), and not 3 (A twice + B).
    experiences = {}

    def get_experience(exp_type, item, day, default):
        if exp_type not in experiences:
            experiences[exp_type] = {}

        if item not in experiences[exp_type]:
            experiences[exp_type][item] = exp_queue(
                day, EXPERIENCE_TIMESPAN + 1, default
            )

        return experiences[exp_type][item][day]

    def update_experiences(experience_type, day, items):
        total_exps = [get_experience(experience_type, item, day, 0) for item in items]
        timespan_exps = [
            exp - get_experience(experience_type, item, day - EXPERIENCE_TIMESPAN, 0)
            for exp, item in zip(total_exps, items)
        ]

        total_exps_sum = sum(total_exps)
        timespan_exps_sum = sum(timespan_exps)

        commit.set_experience(
            experience_type,
            "total",
            total_exps_sum,
            max(total_exps, default=0),
            min(total_exps, default=0),
        )
        commit.set_experience(
            experience_type,
            EXPERIENCE_TIMESPAN_TEXT,
            timespan_exps_sum,
            max(timespan_exps, default=0),
            min(timespan_exps, default=0),
        )

        # We don't want to consider backed out commits when calculating experiences.
        if commit.ever_backedout:
            for i, item in enumerate(items):
                experiences[experience_type][item][day] = total_exps[i] + 1

    def update_complex_experiences(experience_type, day, items):
        all_commit_lists = [
            get_experience(experience_type, item, day, tuple()) for item in items
        ]
        before_commit_lists = [
            get_experience(experience_type, item, day - EXPERIENCE_TIMESPAN, tuple())
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

        # We don't want to consider backed out commits when calculating experiences.
        if commit.ever_backedout:
            for i, item in enumerate(items):
                experiences[experience_type][item][day] = all_commit_lists[i] + (
                    commit.node,
                )

    for commit in tqdm(commits):
        day = (commit.pushdate - first_pushdate).days
        assert day >= 0

        update_experiences("author", day, [commit.author])
        update_experiences("reviewer", day, commit.reviewers)

        # When a file is moved/copied, copy original experience values to the copied path.
        if len(commit.file_copies) > 0:
            for orig, copied in commit.file_copies.items():
                orig_directories = get_directories(orig)
                copied_directories = get_directories(copied)
                for orig_directory, copied_directory in zip(
                    orig_directories, copied_directories
                ):
                    experiences["directory"][copied_directory] = copy.deepcopy(
                        experiences["directory"][orig_directory]
                    )

                if orig in path_to_component and copied in path_to_component:
                    orig_component = path_to_component[orig]
                    copied_component = path_to_component[copied]
                    experiences["component"][copied_component] = copy.deepcopy(
                        experiences["component"][orig_component]
                    )

                experiences["file"][copied] = copy.deepcopy(experiences["file"][orig])

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


def download_commits(repo_dir, date_from):
    hg = hglib.open(repo_dir)

    revs = get_revs(hg)

    assert (
        len(revs) > 0
    ), "There should definitely be more than 0 commits, something is wrong"

    hg.close()

    # Skip commits which are in .hg-annotate-ignore-revs (mostly consisting of very
    # large and not meaningful formatting changes).
    with open(os.path.join(repo_dir, ".hg-annotate-ignore-revs"), "rb") as f:
        ignore_revs = set(l[:40] for l in f)

    revs = [rev for rev in revs if rev not in ignore_revs]

    processes = multiprocessing.cpu_count()

    print(f"Mining {len(revs)} commits using {processes} processes...")

    CHUNK_SIZE = int(math.ceil(len(revs) / processes))
    REVS_COUNT = len(revs)

    # revs_groups contains exactly num_processes elements so no need to have chunksize != 1
    revs_groups = [
        (revs[i], revs[min(i + CHUNK_SIZE, REVS_COUNT) - 1])
        for i in range(0, REVS_COUNT, CHUNK_SIZE)
    ]

    with concurrent.futures.ProcessPoolExecutor(
        initializer=_init, initargs=(repo_dir,)
    ) as executor:
        commits = executor.map(_hg_log, revs_groups, chunksize=1)
        commits = tqdm(commits, total=len(revs_groups))
        commits = list(itertools.chain.from_iterable(commits))

    # Don't analyze backouts.
    backouts = set(commit.backedoutby for commit in commits if commit.ever_backedout)
    commits = [commit for commit in commits if commit.node not in backouts]

    # Don't analyze commits that are not linked to a bug.
    commits = [commit for commit in commits if commit.bug_id is not None]

    print("Downloading file->component mapping...")

    global path_to_component
    r = requests.get(
        "https://index.taskcluster.net/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json"
    )
    r.raise_for_status()
    path_to_component = r.json()
    path_to_component = {
        path: "::".join(component) for path, component in path_to_component.items()
    }

    commits_num = len(commits)

    print(f"Mining {commits_num} commits using {processes} processes...")

    global rs_parsepatch
    import rs_parsepatch

    # Exclude commits outside the range we care about.
    recent_commits = [commit for commit in commits if commit.pushdate > date_from]
    recent_commits_num = len(recent_commits)

    with concurrent.futures.ProcessPoolExecutor(
        initializer=_init, initargs=(repo_dir,)
    ) as executor:
        recent_commits = executor.map(_transform, recent_commits, chunksize=64)
        recent_commits = tqdm(recent_commits, total=recent_commits_num)
        recent_commits = list(recent_commits)

    calculate_experiences(commits)
    db.write(COMMITS_DB, (commit.to_dict() for commit in commits))


def get_commit_map():
    commit_map = {}

    for commit in get_commits():
        bug_id = commit["bug_id"]

        if not bug_id:
            continue

        if bug_id not in commit_map:
            commit_map[bug_id] = []

        commit_map[bug_id].append(commit)

    return commit_map


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repository_dir", help="Path to the repository", action="store")
    args = parser.parse_args()

    two_years_and_six_months_ago = datetime.utcnow() - relativedelta(years=2, months=6)

    download_commits(args.repository_dir, two_years_and_six_months_ago)
