# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import copy
import io
import itertools
import json
import logging
import math
import os
import pickle
import shelve
import subprocess
import sys
import threading
from datetime import datetime
from functools import lru_cache

import hglib
import lmdb
from tqdm import tqdm

from bugbug import db, rust_code_analysis_server, utils
from bugbug.utils import LMDBDict, get_hgmo_patch

logger = logging.getLogger(__name__)

hg_servers = list()
hg_servers_lock = threading.Lock()
thread_local = threading.local()

COMMITS_DB = "data/commits.json"
COMMIT_EXPERIENCES_DB = "commit_experiences.lmdb.tar.zst"
db.register(
    COMMITS_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/commits.json.zst",
    10,
    [COMMIT_EXPERIENCES_DB],
)

path_to_component = None

EXPERIENCE_TIMESPAN = 90
EXPERIENCE_TIMESPAN_TEXT = f"{EXPERIENCE_TIMESPAN}_days"

SOURCE_CODE_TYPES_TO_EXT = {
    "Assembly": [".asm", ".S"],
    "Javascript": [".js", ".jsm", ".sjs"],
    "C/C++": [".c", ".cpp", ".cc", ".cxx", ".m", ".mm", ".h", ".hh", ".hpp", ".hxx"],
    "Java": [".java"],
    "Python": [".py"],
    "Rust": [".rs"],
    "Kotlin": [".kt"],
    "HTML/XHTML/XUL": [".html", ".htm", ".xhtml", ".xht", ".xul"],
    "IDL/IPDL/WebIDL": [".idl", ".ipdl", ".webidl"],
}

OTHER_TYPES_TO_EXT = {
    "YAML": [".yaml", ".yml"],
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
    "Documentation": [".rst", ".md"],
    "Build System File": [".build", ".mk", ".in"],
}

HARDCODED_TYPES = {".eslintrc.js": ".eslintrc.js"}

TYPES_TO_EXT = {**SOURCE_CODE_TYPES_TO_EXT, **OTHER_TYPES_TO_EXT}

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
        self.source_code_added = 0
        self.other_added = 0
        self.test_added = 0
        self.source_code_deleted = 0
        self.other_deleted = 0
        self.test_deleted = 0
        self.types = set()
        self.functions = {}
        self.seniority_author = 0.0
        self.total_source_code_file_size = 0
        self.average_source_code_file_size = 0
        self.maximum_source_code_file_size = 0
        self.minimum_source_code_file_size = 0
        self.source_code_files_modified_num = 0
        self.total_other_file_size = 0
        self.average_other_file_size = 0
        self.maximum_other_file_size = 0
        self.minimum_other_file_size = 0
        self.other_files_modified_num = 0
        self.total_test_file_size = 0
        self.average_test_file_size = 0
        self.maximum_test_file_size = 0
        self.minimum_test_file_size = 0
        self.test_files_modified_num = 0
        self.average_cyclomatic = 0.0
        self.average_halstead_operands = 0.0
        self.average_halstead_unique_operands = 0.0
        self.average_halstead_operators = 0.0
        self.average_halstead_unique_operators = 0.0
        self.average_source_loc = 0.0
        self.average_logical_loc = 0.0
        self.maximum_cyclomatic = 0
        self.maximum_halstead_operands = 0
        self.maximum_halstead_unique_operands = 0
        self.maximum_halstead_operators = 0
        self.maximum_halstead_unique_operators = 0
        self.maximum_source_loc = 0
        self.maximum_logical_loc = 0
        self.minimum_cyclomatic = sys.maxsize
        self.minimum_halstead_operands = sys.maxsize
        self.minimum_halstead_unique_operands = sys.maxsize
        self.minimum_halstead_operators = sys.maxsize
        self.minimum_halstead_unique_operators = sys.maxsize
        self.minimum_source_loc = sys.maxsize
        self.minimum_logical_loc = sys.maxsize
        self.total_cyclomatic = 0
        self.total_halstead_operands = 0
        self.total_halstead_unique_operands = 0
        self.total_halstead_operators = 0
        self.total_halstead_unique_operators = 0
        self.total_source_loc = 0
        self.total_logical_loc = 0

    def __eq__(self, other):
        assert isinstance(other, Commit)
        return self.node == other.node

    def __hash__(self):
        return hash(self.node)

    def set_files(self, files, file_copies):
        self.files = files
        self.file_copies = file_copies
        self.components = list(
            set(
                path_to_component[path.encode("utf-8")].tobytes().decode("utf-8")
                for path in files
                if path.encode("utf-8") in path_to_component
            )
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


def _init_process(repo_dir):
    global HG, REPO_DIR
    REPO_DIR = repo_dir
    HG = hglib.open(REPO_DIR)
    get_component_mapping()


def _init_thread(repo_dir):
    hg_server = hglib.open(repo_dir)
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
        branch="tip",
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
        [sys.intern(f) for f in files_str.decode("utf-8").split("|") if f], file_copies
    )


def get_touched_functions(path, deleted_lines, added_lines, content):
    if content is None:
        return set()

    function_data = code_analysis_server.function(path, content)
    if not function_data:
        return set()

    touched_functions = set()
    touched_function_names = set()

    functions = function_data["spans"]
    functions = [
        function
        for function in functions
        if not function["error"] and function["name"] != "<anonymous>"
    ]

    def get_touched(functions, lines):
        last_f = 0
        for line in lines:
            for function in functions[last_f:]:
                # Skip functions which we already passed.
                if function["end_line"] < line:
                    last_f += 1

                # If the line belongs to this function, add the function to the set of touched functions.
                elif function["start_line"] <= line:
                    touched_function_names.add(function["name"])
                    last_f += 1

    # Get functions touched by added lines.
    get_touched(functions, added_lines)

    # Map functions to their positions before the patch.
    prev_functions = copy.deepcopy(functions)

    for line in added_lines:
        for func in prev_functions:
            if line < func["start_line"]:
                func["start_line"] -= 1

            if line < func["end_line"]:
                func["end_line"] -= 1

    for line in deleted_lines:
        for func in prev_functions:
            if line < func["start_line"]:
                func["start_line"] += 1

            if line < func["end_line"]:
                func["end_line"] += 1

    # Get functions touched by removed lines.
    get_touched(prev_functions, deleted_lines)

    # Return touched functions, with their new positions.
    for function in functions:
        if function["name"] in touched_function_names:
            touched_functions.add(
                (function["name"], function["start_line"], function["end_line"])
            )

    return touched_functions


def get_metrics(commit, metrics_space):
    if metrics_space["kind"] == "function":
        metrics = metrics_space["metrics"]
        commit.total_cyclomatic += metrics["cyclomatic"]
        commit.total_halstead_unique_operands += metrics["halstead"]["unique_operands"]
        commit.total_halstead_operands += metrics["halstead"]["operands"]
        commit.total_halstead_unique_operators += metrics["halstead"][
            "unique_operators"
        ]
        commit.total_halstead_operators += metrics["halstead"]["operators"]
        commit.total_source_loc += metrics["loc"]["sloc"]
        commit.total_logical_loc += metrics["loc"]["lloc"]

        commit.maximum_cyclomatic = max(
            commit.maximum_cyclomatic, metrics["cyclomatic"]
        )
        commit.maximum_halstead_unique_operands = max(
            commit.maximum_halstead_unique_operands,
            metrics["halstead"]["unique_operands"],
        )
        commit.maximum_halstead_operands = max(
            metrics["halstead"]["operands"], commit.maximum_halstead_operands
        )
        commit.maximum_halstead_unique_operators = max(
            metrics["halstead"]["unique_operators"],
            commit.maximum_halstead_unique_operators,
        )
        commit.maximum_halstead_operators = max(
            metrics["halstead"]["operators"], commit.maximum_halstead_operators
        )
        commit.maximum_source_loc = max(
            metrics["loc"]["sloc"], commit.maximum_source_loc
        )
        commit.maximum_logical_loc = max(
            metrics["loc"]["lloc"], commit.maximum_logical_loc
        )

        commit.minimum_cyclomatic = min(
            commit.minimum_cyclomatic, metrics["cyclomatic"]
        )
        commit.minimum_halstead_unique_operands = min(
            commit.minimum_halstead_unique_operands,
            metrics["halstead"]["unique_operands"],
        )
        commit.minimum_halstead_operands = min(
            metrics["halstead"]["operands"], commit.minimum_halstead_operands
        )
        commit.minimum_halstead_unique_operators = min(
            metrics["halstead"]["unique_operators"],
            commit.minimum_halstead_unique_operators,
        )
        commit.minimum_halstead_operators = min(
            metrics["halstead"]["operators"], commit.minimum_halstead_operators
        )
        commit.minimum_source_loc = min(
            metrics["loc"]["sloc"], commit.minimum_source_loc
        )
        commit.minimum_logical_loc = min(
            metrics["loc"]["lloc"], commit.minimum_logical_loc
        )

    for space in metrics_space["spaces"]:
        get_metrics(commit, space)


def transform(hg, repo_dir, commit):
    hg_modified_files(hg, commit)

    if commit.ignored:
        return commit

    source_code_sizes = []
    other_sizes = []
    test_sizes = []
    metrics_file_count = 0

    patch = hg.export(revs=[commit.node.encode("ascii")], git=True)
    patch_data = rs_parsepatch.get_lines(patch)
    for stats in patch_data:
        path = stats["filename"]

        if stats["binary"]:
            if not is_test(path):
                commit.types.add("binary")
            continue

        size = None
        after = None
        if not stats["deleted"]:
            try:
                after = hg.cat(
                    [os.path.join(repo_dir, path).encode("utf-8")],
                    rev=commit.node.encode("ascii"),
                )
                size = after.count(b"\n")
            except hglib.error.CommandError as e:
                if b"no such file in rev" not in e.err:
                    raise

        file_name = os.path.basename(path)
        if file_name in HARDCODED_TYPES:
            type_ = HARDCODED_TYPES[file_name]
        else:
            ext = os.path.splitext(path)[1].lower()
            type_ = EXT_TO_TYPES.get(ext, ext)

        if is_test(path):
            commit.test_files_modified_num += 1

            commit.test_added += len(stats["added_lines"])
            commit.test_deleted += len(stats["deleted_lines"])

            if size is not None:
                test_sizes.append(size)

            # We don't have a 'test' equivalent of types, as most tests are JS,
            # so this wouldn't add useful information.
        elif type_ in SOURCE_CODE_TYPES_TO_EXT:
            commit.source_code_files_modified_num += 1

            touched_functions = get_touched_functions(
                path, stats["deleted_lines"], stats["added_lines"], after
            )
            if len(touched_functions) > 0:
                commit.functions[path] = list(touched_functions)

            commit.source_code_added += len(stats["added_lines"])
            commit.source_code_deleted += len(stats["deleted_lines"])

            if size is not None:
                source_code_sizes.append(size)
                metrics = code_analysis_server.metrics(path, after, unit=False)
                if metrics.get("spaces"):
                    metrics_file_count += 1
                    get_metrics(commit, metrics["spaces"])

                # Add "Objective-C/C++" type if rust-code-analysis detected this is an Objective-C/C++ file.
                # We use both C/C++ and Objective-C/C++ as Objective-C/C++ files are few but share most characteristics
                # with C/C++ files: we don't want to lose this information by just overwriting the type, but we want
                # the additional information that it is an Objective-C/C++ file.
                if type_ == "C/C++" and (
                    metrics.get("language") == "obj-c/c++" or ext in {".m", ".mm"}
                ):
                    commit.types.add("Objective-C/C++")

            commit.types.add(type_)
        else:
            commit.other_files_modified_num += 1

            commit.other_added += len(stats["added_lines"])
            commit.other_deleted += len(stats["deleted_lines"])

            if size is not None:
                other_sizes.append(size)

            if type_:
                commit.types.add(type_)

    commit.total_source_code_file_size = sum(source_code_sizes)
    commit.average_source_code_file_size = (
        commit.total_source_code_file_size / len(source_code_sizes)
        if len(source_code_sizes) > 0
        else 0
    )
    commit.maximum_source_code_file_size = max(source_code_sizes, default=0)
    commit.minimum_source_code_file_size = min(source_code_sizes, default=0)

    commit.total_other_file_size = sum(other_sizes)
    commit.average_other_file_size = (
        commit.total_other_file_size / len(other_sizes) if len(other_sizes) > 0 else 0
    )
    commit.maximum_other_file_size = max(other_sizes, default=0)
    commit.minimum_other_file_size = min(other_sizes, default=0)

    commit.total_test_file_size = sum(test_sizes)
    commit.average_test_file_size = (
        commit.total_test_file_size / len(test_sizes) if len(test_sizes) > 0 else 0
    )
    commit.maximum_test_file_size = max(test_sizes, default=0)
    commit.minimum_test_file_size = min(test_sizes, default=0)

    if metrics_file_count:
        commit.average_cyclomatic = commit.total_cyclomatic / metrics_file_count
        commit.average_halstead_unique_operands = (
            commit.total_halstead_unique_operands / metrics_file_count
        )
        commit.average_halstead_operands = (
            commit.total_halstead_operands / metrics_file_count
        )
        commit.average_halstead_unique_operators = (
            commit.total_halstead_unique_operators / metrics_file_count
        )
        commit.average_halstead_operators = (
            commit.total_halstead_operators / metrics_file_count
        )
        commit.average_source_loc = commit.total_source_loc / metrics_file_count
        commit.average_logical_loc = commit.total_logical_loc / metrics_file_count
    else:
        # these values are initialized with sys.maxsize (because we take the min)
        # if no files, then reset them to 0 (it'd be stupid to have min > max)
        commit.minimum_cyclomatic = 0
        commit.minimum_halstead_operands = 0
        commit.minimum_halstead_unique_operands = 0
        commit.minimum_halstead_operators = 0
        commit.minimum_halstead_unique_operators = 0
        commit.minimum_source_loc = 0
        commit.minimum_logical_loc = 0

    return commit


def _transform(commit):
    return transform(HG, REPO_DIR, commit)


def hg_log(hg, revs):
    template = "{node}\\0{author}\\0{desc}\\0{date|hgdate}\\0{bug}\\0{backedoutby}\\0{author|email}\\0{pushdate|hgdate}\\0{reviewers}\\0"

    args = hglib.util.cmdbuilder(
        b"log",
        template=template,
        no_merges=True,
        rev=revs[0] + b":" + revs[-1],
        branch="tip",
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
            list(set(sys.intern(r) for r in rev[8].decode("utf-8").split(" ")))
            if rev[8] != b""
            else []
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
                reviewers=reviewers,
            )
        )

    return revs


def _hg_log(revs):
    return hg_log(thread_local.hg, revs)


def get_revs(hg, rev_start=0, rev_end="tip"):
    logger.info(f"Getting revs from {rev_start} to {rev_end}...")

    args = hglib.util.cmdbuilder(
        b"log",
        template="{node}\n",
        no_merges=True,
        branch="tip",
        rev=f"{rev_start}:{rev_end}",
    )
    x = hg.rawcommand(args)
    return x.splitlines()


class Experiences:
    def __init__(self, save):
        self.save = save

        try:
            self.db_experiences = shelve.Shelf(
                LMDBDict("data/commit_experiences.lmdb", readonly=not save),
                protocol=pickle.DEFAULT_PROTOCOL,
                writeback=save,
            )
        except lmdb.Error as e:
            if not save and "No such file or directory" in str(e):
                self.db_experiences = {}
            else:
                raise

        if not save:
            self.mem_experiences = {}

    def __contains__(self, key):
        if self.save:
            return key in self.db_experiences
        else:
            return key in self.mem_experiences or key in self.db_experiences

    def __getitem__(self, key):
        if self.save:
            return self.db_experiences[key]
        else:
            return (
                self.mem_experiences[key]
                if key in self.mem_experiences
                else self.db_experiences[key]
            )

    def __setitem__(self, key, value):
        if self.save:
            self.db_experiences[key] = value
        else:
            self.mem_experiences[key] = value


def calculate_experiences(commits, first_pushdate, save=True):
    logger.info(f"Analyzing seniorities from {len(commits)} commits...")

    experiences = Experiences(save)

    for commit in tqdm(commits):
        key = f"first_commit_time${commit.author}"
        if key not in experiences:
            experiences[key] = commit.pushdate
            commit.seniority_author = 0
        else:
            time_lapse = commit.pushdate - experiences[key]
            commit.seniority_author = time_lapse.total_seconds()

    logger.info(f"Analyzing experiences from {len(commits)} commits...")

    # Note: In the case of files, directories, components, we can't just use the sum of previous commits, as we could end
    # up overcounting them. For example, consider a commit A which modifies "dir1" and "dir2", a commit B which modifies
    # "dir1" and a commit C which modifies "dir1" and "dir2". The number of previous commits touching the same directories
    # for C should be 2 (A + B), and not 3 (A twice + B).

    def get_key(exp_type, commit_type, item):
        return f"{exp_type}${commit_type}${item}"

    def get_experience(exp_type, commit_type, item, day, default):
        key = get_key(exp_type, commit_type, item)
        if key not in experiences:
            experiences[key] = utils.ExpQueue(day, EXPERIENCE_TIMESPAN + 1, default)
        return experiences[key]

    def update_experiences(experience_type, day, items):
        for commit_type in ["", "backout"]:
            exp_queues = [
                get_experience(experience_type, commit_type, item, day, 0)
                for item in items
            ]
            total_exps = [exp_queues[i][day] for i in range(len(items))]
            timespan_exps = [
                exp - exp_queues[i][day - EXPERIENCE_TIMESPAN]
                for exp, i in zip(total_exps, range(len(items)))
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
                for i in range(len(items)):
                    exp_queues[i][day] = total_exps[i] + 1

    def update_complex_experiences(experience_type, day, items):
        for commit_type in ["", "backout"]:
            exp_queues = [
                get_experience(experience_type, commit_type, item, day, tuple())
                for item in items
            ]
            all_commit_lists = [exp_queues[i][day] for i in range(len(items))]
            before_commit_lists = [
                exp_queues[i][day - EXPERIENCE_TIMESPAN] for i in range(len(items))
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
                for i in range(len(items)):
                    exp_queues[i][day] = all_commit_lists[i] + (commit.node,)

    # prev_day = 0
    # prev_commit = None
    for i, commit in enumerate(tqdm(commits)):
        day = (commit.pushdate - first_pushdate).days
        assert day >= 0
        # TODO: Bring back this assertion, but using pushid instead.
        # pushdate is unreliable, e.g. 4d0e3037210dd03bdb21964a6a8c2e201c45794b was pushed after 06b578dfadc9db8b683090e0e110ba75b84fb766, but it has an earlier push date...
        # assert (
        #     day >= prev_day
        # ), f"Commit {commit.node} pushed on {commit.pushdate} should come after {prev_commit.node} pushed on {prev_commit.pushdate}"
        # prev_day = day
        # prev_commit = commit

        # When a file is moved/copied, copy original experience values to the copied path.
        for orig, copied in commit.file_copies.items():
            for commit_type in ["", "backout"]:
                orig_key = get_key("file", commit_type, orig)
                if orig_key in experiences:
                    experiences[get_key("file", commit_type, copied)] = copy.deepcopy(
                        experiences[orig_key]
                    )
                else:
                    logger.warning(
                        f"Experience missing for file {orig}, type '{commit_type}', on commit {commit.node}"
                    )

        if not commit.ignored:
            update_experiences("author", day, [commit.author])
            update_experiences("reviewer", day, commit.reviewers)

            update_complex_experiences("file", day, commit.files)
            update_complex_experiences("directory", day, commit.directories)
            update_complex_experiences("component", day, commit.components)


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
    path_to_component = get_component_mapping(False)

    utils.download_check_etag(
        "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json",
        "data/component_mapping.json",
    )

    with open("data/component_mapping.json", "r") as f:
        data = json.load(f)

    for path, component in data.items():
        path_to_component[path.encode("utf-8")] = "::".join(component).encode("utf-8")

    close_component_mapping()


def get_component_mapping(readonly=True):
    global path_to_component
    path_to_component = LMDBDict("data/component_mapping.lmdb", readonly=readonly)
    return path_to_component


def close_component_mapping():
    global path_to_component
    path_to_component.close()
    path_to_component = None


def hg_log_multi(repo_dir, revs):
    if len(revs) == 0:
        return []

    threads_num = os.cpu_count() + 1
    REVS_COUNT = len(revs)
    CHUNK_SIZE = int(math.ceil(REVS_COUNT / threads_num))
    revs_groups = [
        (revs[i], revs[min(i + CHUNK_SIZE, REVS_COUNT) - 1])
        for i in range(0, REVS_COUNT, CHUNK_SIZE)
    ]

    with concurrent.futures.ThreadPoolExecutor(
        initializer=_init_thread, initargs=(repo_dir,), max_workers=threads_num
    ) as executor:
        commits = executor.map(_hg_log, revs_groups)
        commits = tqdm(commits, total=len(revs_groups))
        commits = list(itertools.chain.from_iterable(commits))

    while len(hg_servers) > 0:
        hg_server = hg_servers.pop()
        hg_server.close()

    return commits


@lru_cache(maxsize=None)
def get_first_pushdate(repo_dir):
    with hglib.open(repo_dir) as hg:
        return hg_log(hg, [b"0"])[0].pushdate


def download_commits(
    repo_dir, rev_start=None, revs=None, save=True, use_single_process=False
):
    assert revs is not None or rev_start is not None

    with hglib.open(repo_dir) as hg:
        if revs is None:
            revs = get_revs(hg, rev_start)
            if len(revs) == 0:
                logger.info("No commits to analyze")
                return []

        first_pushdate = get_first_pushdate(repo_dir)

        logger.info(f"Mining {len(revs)} commits...")

        if not use_single_process:
            logger.info(f"Using {os.cpu_count()} processes...")
            commits = hg_log_multi(repo_dir, revs)
        else:
            commits = hg_log(hg, revs)

        if save:
            logger.info("Downloading file->component mapping...")
            download_component_mapping()

        set_commits_to_ignore(repo_dir, commits)

        commits_num = len(commits)

        logger.info(f"Mining {commits_num} patches...")

        global rs_parsepatch
        import rs_parsepatch

        global code_analysis_server
        code_analysis_server = rust_code_analysis_server.RustCodeAnalysisServer()

        if not use_single_process:
            with concurrent.futures.ProcessPoolExecutor(
                initializer=_init_process, initargs=(repo_dir,)
            ) as executor:
                commits = executor.map(_transform, commits, chunksize=64)
                commits = tqdm(commits, total=commits_num)
                commits = list(commits)
        else:
            get_component_mapping()

            commits = [transform(hg, repo_dir, c) for c in commits]

            close_component_mapping()

    code_analysis_server.terminate()

    calculate_experiences(commits, first_pushdate, save)

    logger.info(f"Applying final commits filtering...")

    commits = [commit.to_dict() for commit in commits if not commit.ignored]

    if save:
        db.append(COMMITS_DB, commits)

    return commits


def clean(hg, repo_dir, pull=True):
    logger.info("Restoring files to their checkout state...")
    hg.revert(repo_dir.encode("utf-8"), all=True)

    logger.info("Stripping non-public commits...")
    try:
        cmd = hglib.util.cmdbuilder(
            b"strip", rev=b"roots(outgoing())", force=True, backup=False
        )
        hg.rawcommand(cmd)
    except hglib.error.CommandError as e:
        if b"abort: empty revision set" not in e.err:
            raise

    # Pull and update.
    if pull:
        logger.info(f"Pulling and updating {repo_dir}")
        hg.pull(update=True)
        logger.info(f"{repo_dir} pulled and updated")


def clone(repo_dir, url="https://hg.mozilla.org/mozilla-central"):
    try:
        with hglib.open(repo_dir) as hg:
            clean(hg, repo_dir)

        return
    except hglib.error.ServerError as e:
        if "abort: repository" not in str(e) and b"not found" not in str(e):
            raise

    cmd = hglib.util.cmdbuilder(
        "robustcheckout",
        url,
        repo_dir,
        purge=True,
        sharebase=repo_dir + "-shared",
        networkattempts=7,
        branch=b"tip",
    )

    cmd.insert(0, hglib.HGPATH)

    subprocess.run(cmd, check=True)

    logger.info(f"{repo_dir} cloned")

    # Remove pushlog DB to make sure it's regenerated.
    try:
        os.remove(os.path.join(repo_dir, ".hg", "pushlog2.db"))
    except FileNotFoundError:
        logger.info("pushlog database doesn't exist")

    # Pull and update, to make sure the pushlog is generated.
    with hglib.open(repo_dir) as hg:
        clean(hg, repo_dir)


def apply_stack(repo_dir, stack, branch):
    """Apply a stack of patches on a repository"""
    assert len(stack) > 0, "Empty stack"

    def has_revision(revision):
        try:
            hg.identify(revision)
            return True
        except hglib.error.CommandError:
            return False

    def apply_patches(base, patches):
        # Update to base revision
        logger.info(f"Updating {repo_dir} to {base}")
        hg.update(base, clean=True)

        # Then apply each patch in the stack
        logger.info(f"Applying {len(patches)}...")
        try:
            for node, patch in patches:
                hg.import_(patches=io.BytesIO(patch.encode("utf-8")), user="bugbug")
        except hglib.error.CommandError as e:
            logger.warning(f"Failed to apply patch {node}: {e.err}")
            return False
        return True

    # Find the base revision to apply all the patches onto
    # Use first parent from first patch if all its parents are available
    # Otherwise fallback on tip
    def get_base():
        parents = stack[0]["parents"]
        assert len(parents) > 0, "No parents found for first patch"
        if all(map(has_revision, parents)):
            return parents[0]

        return "tip"

    def stack_nodes():
        return get_revs(hg, f"-{len(stack)}")

    with hglib.open(repo_dir) as hg:
        # Start by cleaning the repo, without pulling
        clean(hg, repo_dir, pull=False)
        logger.info("Repository cleaned")

        # Get initial base revision
        base = get_base()

        # Load all the patches in the stack
        patches = [(rev["node"], get_hgmo_patch(branch, rev["node"])) for rev in stack]
        logger.info(f"Loaded {len(patches)} patches for the stack")

        # Apply all the patches in the stack on current base
        if apply_patches(base, patches):
            logger.info(f"Stack applied successfully on {base}")
            return stack_nodes()

        # We tried to apply on the valid parent and failed: cannot try another revision
        if base != "tip":
            raise Exception(f"Failed to apply on valid parent {base}")

        # We tried to apply on tip, let's try to find the valid parent after pulling
        clean(hg, repo_dir, pull=True)

        # Check if the valid base is now available
        new_base = get_base()
        if base == new_base:
            raise Exception("No valid parent found for the stack")

        if not apply_patches(new_base, patches):
            raise Exception("Failed to apply stack on second try")

        logger.info(f"Stack applied successfully on second try on {new_base}")

        return stack_nodes()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repository_dir", help="Path to the repository", action="store")
    parser.add_argument(
        "rev_start", help="Which revision to start with", action="store"
    )
    args = parser.parse_args()

    download_commits(args.repository_dir, args.rev_start, save=False)
