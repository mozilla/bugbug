# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import copy
import itertools
import multiprocessing
import os
import re
from collections import defaultdict, namedtuple
from datetime import datetime

import hglib
import requests
from dateutil.relativedelta import relativedelta
from parsepatch.patch import Patch
from tqdm import tqdm

from bugbug import db

COMMITS_DB = "data/commits.json"
db.register(
    COMMITS_DB,
    "https://index.taskcluster.net/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/commits.json.xz",
)

path_to_component = {}

Commit = namedtuple(
    "Commit",
    [
        "node",
        "author",
        "desc",
        "date",
        "pushdate",
        "bug",
        "backedoutby",
        "author_email",
        "files",
        "file_copies",
        "reviewers",
    ],
)

EXPERIENCE_TIMESPAN = 90
EXPERIENCE_TIMESPAN_TEXT = f"{EXPERIENCE_TIMESPAN}_days"

experiences_by_commit = {
    "total": defaultdict(lambda: defaultdict(int)),
    EXPERIENCE_TIMESPAN_TEXT: defaultdict(lambda: defaultdict(int)),
}

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


def get_commits():
    return db.read(COMMITS_DB)


def _init(repo_dir):
    global HG
    HG = hglib.open(repo_dir)


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
    desc = commit.desc.decode("utf-8")

    obj = {
        "node": commit.node.decode("utf-8"),
        "author": commit.author.decode("utf-8"),
        "reviewers": commit.reviewers,
        "desc": desc,
        "date": str(commit.date),
        "bug_id": int(commit.bug.decode("utf-8")) if commit.bug else None,
        "ever_backedout": commit.backedoutby != b"",
        "added": 0,
        "test_added": 0,
        "deleted": 0,
        "test_deleted": 0,
        "files_modified_num": 0,
        "types": set(),
        "components": list(),
        "author_experience": experiences_by_commit["total"]["author"][commit.node],
        f"author_experience_{EXPERIENCE_TIMESPAN_TEXT}": experiences_by_commit[
            EXPERIENCE_TIMESPAN_TEXT
        ]["author"][commit.node],
        "reviewer_experience": experiences_by_commit["total"]["reviewer"][commit.node],
        f"reviewer_experience_{EXPERIENCE_TIMESPAN_TEXT}": experiences_by_commit[
            EXPERIENCE_TIMESPAN_TEXT
        ]["reviewer"][commit.node],
        "author_email": commit.author_email.decode("utf-8"),
        "components_touched_prev": experiences_by_commit["total"]["component"][
            commit.node
        ],
        f"components_touched_prev_{EXPERIENCE_TIMESPAN_TEXT}": experiences_by_commit[
            EXPERIENCE_TIMESPAN_TEXT
        ]["component"][commit.node],
        "files_touched_prev": experiences_by_commit["total"]["file"][commit.node],
        f"files_touched_prev_{EXPERIENCE_TIMESPAN_TEXT}": experiences_by_commit[
            EXPERIENCE_TIMESPAN_TEXT
        ]["file"][commit.node],
        "directories_touched_prev": experiences_by_commit["total"]["directory"][
            commit.node
        ],
        f"directories_touched_prev_{EXPERIENCE_TIMESPAN_TEXT}": experiences_by_commit[
            EXPERIENCE_TIMESPAN_TEXT
        ]["directory"][commit.node],
    }

    patch = HG.export(revs=[commit.node], git=True)
    patch_data = Patch.parse_patch(
        patch.decode("utf-8", "ignore"), skip_comments=False, add_lines_for_new=True
    )
    for path, stats in patch_data.items():
        if "added" not in stats:
            # Must be a binary file
            obj["types"].add("binary")
            continue

        if is_test(path):
            obj["test_added"] += len(stats["added"]) + len(stats["touched"])
            obj["test_deleted"] += len(stats["deleted"]) + len(stats["touched"])
        else:
            obj["added"] += len(stats["added"]) + len(stats["touched"])
            obj["deleted"] += len(stats["deleted"]) + len(stats["touched"])

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

    obj["files_modified_num"] = len(patch_data)

    # Covert to a list, as a set is not JSON-serializable.
    obj["types"] = list(obj["types"])

    obj["components"] = list(
        set(
            path_to_component[path]
            for path in patch_data.keys()
            if path_to_component.get(path)
        )
    )

    return obj


def _hg_log(revs):
    template = '{node}\\0{author}\\0{desc}\\0{date}\\0{bug}\\0{backedoutby}\\0{author|email}\\0{join(files,"|")}\\0{join(file_copies,"|")}\\0{pushdate}\\0'

    args = hglib.util.cmdbuilder(
        b"log",
        template=template,
        no_merges=True,
        rev=revs[0] + b":" + revs[-1],
        branch="central",
    )
    x = HG.rawcommand(args)
    out = x.split(b"\x00")[:-1]

    revs = []
    for rev in hglib.util.grouper(template.count("\\0"), out):
        date = datetime.utcfromtimestamp(float(rev[3].split(b".", 1)[0]))

        pushdate = datetime.utcfromtimestamp(float(rev[9].split(b"-", 1)[0]))

        file_copies = {}
        for file_copy in rev[8].decode("utf-8").split("|"):
            if not file_copy:
                continue

            parts = file_copy.split(" (")
            copied = parts[0]
            orig = parts[1][:-1]
            file_copies[orig] = copied

        revs.append(
            Commit(
                node=rev[0],
                author=rev[1],
                desc=rev[2],
                date=date,
                pushdate=pushdate,
                bug=rev[4],
                backedoutby=rev[5],
                author_email=rev[6],
                files=rev[7].decode("utf-8").split("|"),
                file_copies=file_copies,
                reviewers=tuple(get_reviewers(rev[2].decode("utf-8"))),
            )
        )

    return revs


def get_revs(hg):
    args = hglib.util.cmdbuilder(
        b"log", template="{node}\n", no_merges=True, branch="central"
    )
    x = hg.rawcommand(args)
    return x.splitlines()


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


def download_commits(repo_dir, date_from):
    hg = hglib.open(repo_dir)

    revs = get_revs(hg)

    commits_num = len(revs)

    assert (
        commits_num > 0
    ), "There should definitely be more than 0 commits, something is wrong"

    hg.close()

    processes = multiprocessing.cpu_count()

    print(f"Mining {commits_num} commits using {processes} processes...")

    CHUNK_SIZE = 256
    revs_groups = [revs[i : (i + CHUNK_SIZE)] for i in range(0, len(revs), CHUNK_SIZE)]

    with concurrent.futures.ProcessPoolExecutor(
        initializer=_init, initargs=(repo_dir,)
    ) as executor:
        commits = executor.map(_hg_log, revs_groups, chunksize=20)
        commits = tqdm(commits, total=len(revs_groups))
        commits = list(itertools.chain.from_iterable(commits))

    # Don't analyze backouts.
    backouts = set(
        commit.backedoutby for commit in commits if commit.backedoutby != b""
    )
    commits = [commit for commit in commits if commit.node not in backouts]

    # Don't analyze commits that are not linked to a bug.
    commits = [commit for commit in commits if commit.bug != b""]

    # Skip commits which are in .hg-annotate-ignore-revs (mostly consisting of very
    # large and not meaningful formatting changes).
    with open(os.path.join(repo_dir, ".hg-annotate-ignore-revs"), "r") as f:
        ignore_revs = set(l[:40].encode("utf-8") for l in f)

    commits = [commit for commit in commits if commit.node not in ignore_revs]

    commits_num = len(commits)

    print(f"Analyzing {commits_num} patches...")

    global experiences_by_commit

    global path_to_component
    r = requests.get(
        "https://index.taskcluster.net/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json"
    )
    r.raise_for_status()
    path_to_component = r.json()
    path_to_component = {
        path: "::".join(component) for path, component in path_to_component.items()
    }

    first_pushdate = commits[0].pushdate

    experiences = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    # In the case of files, directories, components, we can't just use the sum of previous commits, as we could end
    # up overcounting them. For example, consider a commit A which modifies "dir1" and "dir2", a commit B which modifies
    # "dir1" and a commit C which modifies "dir1" and "dir2". The number of previous commits touching the same directories
    # for C should be 2 (A + B), and not 3 (A twice + B).
    complex_experiences = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))

    def update_experiences(experience_type, day, items):
        for item in items:
            exp = experiences[day][experience_type][item]

            experiences_by_commit["total"][experience_type][commit.node] += exp
            experiences_by_commit[EXPERIENCE_TIMESPAN_TEXT][experience_type][
                commit.node
            ] += (exp - experiences[day - EXPERIENCE_TIMESPAN][experience_type][item])

            # We don't want to consider backed out commits when calculating experiences.
            if not commit.backedoutby:
                experiences[day][experience_type][item] += 1

    def update_complex_experiences(experience_type, day, items):
        all_commits = set()
        before_timespan_commits = set()
        for item in items:
            all_commits.update(complex_experiences[day][experience_type][item])

            before_timespan_commits.update(
                complex_experiences[day - EXPERIENCE_TIMESPAN_TEXT][experience_type][
                    item
                ]
            )

            # We don't want to consider backed out commits when calculating experiences.
            if not commit.backedoutby:
                complex_experiences[day][experience_type][item].add(commit.node)

        experiences_by_commit["total"][experience_type][commit.node] = len(all_commits)
        experiences_by_commit[EXPERIENCE_TIMESPAN_TEXT][experience_type][
            commit.node
        ] = len(all_commits - before_timespan_commits)

    for commit in tqdm(commits):
        days = (commit.pushdate - first_pushdate).days
        assert days >= 0

        if days not in experiences:
            assert days not in complex_experiences
            experiences[days] = copy.deepcopy(experiences[days - 1])
            complex_experiences[days] = copy.deepcopy(complex_experiences[days - 1])

        update_experiences("author", days, [commit.author])
        update_experiences("reviewer", days, commit.reviewers)

        # When a file is moved/copied, copy original experience values to the copied path.
        if len(commit.file_copies) > 0:
            for orig, copied in commit.file_copies.items():
                orig_directories = get_directories(orig)
                copied_directories = get_directories(copied)

                if orig in path_to_component and copied in path_to_component:
                    orig_component = path_to_component[orig]
                    copied_component = path_to_component[copied]
                else:
                    orig_component = copied_component = None

                for prev_day in complex_experiences.keys():
                    if orig_component is not None:
                        complex_experiences[prev_day]["component"][
                            copied_component
                        ] = complex_experiences[prev_day]["component"][orig_component]

                    complex_experiences[prev_day]["file"][copied] = complex_experiences[
                        prev_day
                    ]["file"][orig]

                    for orig_directory, copied_directory in zip(
                        orig_directories, copied_directories
                    ):
                        complex_experiences[prev_day]["directory"][
                            copied_directory
                        ] = complex_experiences[prev_day]["directory"][orig_directory]

        update_complex_experiences("file", days, commit.files)

        update_complex_experiences("directory", days, get_directories(commit.files))

        components = list(
            set(
                path_to_component[path]
                for path in commit.files
                if path in path_to_component
            )
        )

        update_complex_experiences("component", days, components)

        # TODO: We can delete anything older than 90 days at this point.

    # Exclude commits outside the range we care about.
    commits = [commit for commit in commits if commit.pushdate > date_from]

    print(f"Mining commits using {multiprocessing.cpu_count()} processes...")

    with concurrent.futures.ProcessPoolExecutor(
        initializer=_init, initargs=(repo_dir,)
    ) as executor:
        commits = executor.map(_transform, commits, chunksize=64)
        commits = tqdm(commits, total=commits_num)
        db.write(COMMITS_DB, commits)


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
