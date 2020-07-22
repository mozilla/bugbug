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
import multiprocessing
import os
import pickle
import shelve
import subprocess
import sys
import threading
from datetime import datetime
from typing import (
    Collection,
    Dict,
    Generator,
    Iterable,
    List,
    NewType,
    Optional,
    Set,
    Tuple,
    Union,
)

import hglib
import lmdb
import rs_parsepatch
import tenacity
from tqdm import tqdm

from bugbug import db, rust_code_analysis_server, utils
from bugbug.utils import LMDBDict

logger = logging.getLogger(__name__)

CommitDict = NewType("CommitDict", dict)

code_analysis_server: Optional[rust_code_analysis_server.RustCodeAnalysisServer] = None

hg_servers = list()
hg_servers_lock = threading.Lock()
thread_local = threading.local()

COMMITS_DB = "data/commits.json"
COMMIT_EXPERIENCES_DB = "commit_experiences.lmdb.tar.zst"
db.register(
    COMMITS_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_commits.latest/artifacts/public/commits.json.zst",
    14,
    [COMMIT_EXPERIENCES_DB],
)

path_to_component = None

EXPERIENCE_TIMESPAN = 90
EXPERIENCE_TIMESPAN_TEXT = f"{EXPERIENCE_TIMESPAN}_days"

SOURCE_CODE_TYPES_TO_EXT = {
    "Assembly": [".asm", ".S"],
    "Javascript": [".js", ".jsm", ".sjs"],
    "C/C++": [".c", ".cpp", ".cc", ".cxx", ".h", ".hh", ".hpp", ".hxx"],
    "Objective-C/C++": [".mm", ".m"],
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


def get_type(path: str) -> str:
    file_name = os.path.basename(path)
    if file_name in HARDCODED_TYPES:
        return HARDCODED_TYPES[file_name]

    ext = os.path.splitext(path)[1].lower()
    return EXT_TO_TYPES.get(ext, ext)


class Commit:
    def __init__(
        self,
        revision: int,
        node: str,
        author: str,
        desc: str,
        pushdate: datetime,
        bug_id: Optional[int],
        backsout: List[str],
        backedoutby: str,
        author_email: str,
        reviewers: List[str],
        ignored: bool = False,
    ) -> None:
        self.revision = revision
        self.node = node
        self.author = author
        self.bug_id = bug_id
        self.desc = desc
        self.pushdate = pushdate
        self.backsout = backsout
        self.backedoutby = backedoutby
        self.author_email = author_email
        self.reviewers = reviewers
        self.ignored = ignored
        self.source_code_added = 0
        self.other_added = 0
        self.test_added = 0
        self.source_code_deleted = 0
        self.other_deleted = 0
        self.test_deleted = 0
        self.types: Set[str] = set()
        self.functions: Dict[str, List[Tuple[str, int, int]]] = {}
        self.seniority_author = 0.0
        self.total_source_code_file_size = 0
        self.average_source_code_file_size = 0.0
        self.maximum_source_code_file_size = 0
        self.minimum_source_code_file_size = 0
        self.source_code_files_modified_num = 0
        self.total_other_file_size = 0
        self.average_other_file_size = 0.0
        self.maximum_other_file_size = 0
        self.minimum_other_file_size = 0
        self.other_files_modified_num = 0
        self.total_test_file_size = 0
        self.average_test_file_size = 0.0
        self.maximum_test_file_size = 0
        self.minimum_test_file_size = 0
        self.test_files_modified_num = 0
        self.average_cyclomatic = 0.0
        self.average_halstead_N1 = 0.0
        self.average_halstead_n1 = 0.0
        self.average_halstead_N2 = 0.0
        self.average_halstead_n2 = 0.0
        self.average_source_loc = 0.0
        self.average_logical_loc = 0.0
        self.maximum_cyclomatic = 0
        self.maximum_halstead_N2 = 0
        self.maximum_halstead_n2 = 0
        self.maximum_halstead_N1 = 0
        self.maximum_halstead_n1 = 0
        self.maximum_source_loc = 0
        self.maximum_logical_loc = 0
        self.minimum_cyclomatic = sys.maxsize
        self.minimum_halstead_N1 = sys.maxsize
        self.minimum_halstead_n1 = sys.maxsize
        self.minimum_halstead_N2 = sys.maxsize
        self.minimum_halstead_n2 = sys.maxsize
        self.minimum_source_loc = sys.maxsize
        self.minimum_logical_loc = sys.maxsize
        self.total_cyclomatic = 0
        self.total_halstead_N1 = 0
        self.total_halstead_n1 = 0
        self.total_halstead_N2 = 0
        self.total_halstead_n2 = 0
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

    def to_dict(self) -> CommitDict:
        d = self.__dict__
        for f in ["file_copies"]:
            del d[f]
        d["types"] = list(d["types"])
        d["pushdate"] = str(d["pushdate"])
        return CommitDict(d)


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


def is_wptsync(commit: dict) -> bool:
    return commit["author_email"] == "wptsync@mozilla.com" or any(
        s in commit["desc"] for s in ("wpt-pr:", "wpt-head:", "wpt-type:")
    )


def filter_commits(
    commits: Iterable[CommitDict],
    include_no_bug: bool = False,
    include_backouts: bool = False,
    include_ignored: bool = False,
) -> Generator[CommitDict, None, None]:
    for commit in commits:
        if not include_ignored and commit["ignored"]:
            continue

        if not include_no_bug and not commit["bug_id"]:
            continue

        if not include_backouts and len(commit["backsout"]) > 0:
            continue

        yield commit


def get_commits(
    include_no_bug: bool = False,
    include_backouts: bool = False,
    include_ignored: bool = False,
) -> Generator[CommitDict, None, None]:
    return filter_commits(
        db.read(COMMITS_DB),
        include_no_bug=include_no_bug,
        include_backouts=include_backouts,
        include_ignored=include_ignored,
    )


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


def get_functions_from_metrics(metrics_space):
    functions = []

    if (
        metrics_space["kind"] == "function"
        and metrics_space["name"]
        and metrics_space["name"] != "<anonymous>"
    ):
        functions.append(
            {
                "end_line": metrics_space["end_line"],
                "name": metrics_space["name"],
                "start_line": metrics_space["start_line"],
            }
        )

    for space in metrics_space["spaces"]:
        functions += get_functions_from_metrics(space)

    return functions


def get_touched_functions(metrics_space, deleted_lines, added_lines):
    touched_functions = set()
    touched_function_names = set()

    functions = get_functions_from_metrics(metrics_space)

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
    name = metrics_space["name"]
    error = metrics_space["kind"] in {"unit", "function"} and name == ""

    if metrics_space["kind"] == "function" and not error:
        metrics = metrics_space["metrics"]
        commit.total_cyclomatic += metrics["cyclomatic"]
        commit.total_halstead_n2 += metrics["halstead"]["n2"]
        commit.total_halstead_N2 += metrics["halstead"]["N2"]
        commit.total_halstead_n1 += metrics["halstead"]["n1"]
        commit.total_halstead_N1 += metrics["halstead"]["N1"]
        commit.total_source_loc += metrics["loc"]["sloc"]
        commit.total_logical_loc += metrics["loc"]["lloc"]

        commit.maximum_cyclomatic = max(
            commit.maximum_cyclomatic, metrics["cyclomatic"]
        )
        commit.maximum_halstead_n2 = max(
            commit.maximum_halstead_n2, metrics["halstead"]["n2"],
        )
        commit.maximum_halstead_N2 = max(
            metrics["halstead"]["N2"], commit.maximum_halstead_N2
        )
        commit.maximum_halstead_n1 = max(
            metrics["halstead"]["n1"], commit.maximum_halstead_n1,
        )
        commit.maximum_halstead_N1 = max(
            metrics["halstead"]["N1"], commit.maximum_halstead_N1
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
        commit.minimum_halstead_n2 = min(
            commit.minimum_halstead_n2, metrics["halstead"]["n2"],
        )
        commit.minimum_halstead_N2 = min(
            metrics["halstead"]["N2"], commit.minimum_halstead_N2
        )
        commit.minimum_halstead_n1 = min(
            metrics["halstead"]["n1"], commit.minimum_halstead_n1,
        )
        commit.minimum_halstead_N1 = min(
            metrics["halstead"]["N1"], commit.minimum_halstead_N1
        )
        commit.minimum_source_loc = min(
            metrics["loc"]["sloc"], commit.minimum_source_loc
        )
        commit.minimum_logical_loc = min(
            metrics["loc"]["lloc"], commit.minimum_logical_loc
        )

    for space in metrics_space["spaces"]:
        error |= get_metrics(commit, space)

    return error


def transform(hg: hglib.client, repo_dir: str, commit: Commit):
    hg_modified_files(hg, commit)

    if commit.ignored or len(commit.backsout) > 0 or commit.bug_id is None:
        return commit

    assert code_analysis_server is not None

    source_code_sizes = []
    other_sizes = []
    test_sizes = []
    metrics_file_count = 0

    patch = hg.export(revs=[commit.node.encode("ascii")], git=True)
    try:
        patch_data = rs_parsepatch.get_lines(patch)
    except Exception:
        logger.error(f"Exception while analyzing {commit.node}")
        raise

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

        type_ = get_type(path)

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

            commit.source_code_added += len(stats["added_lines"])
            commit.source_code_deleted += len(stats["deleted_lines"])

            if size is not None:
                source_code_sizes.append(size)

                if type_ != "IDL/IPDL/WebIDL":
                    metrics = code_analysis_server.metrics(path, after, unit=False)
                    if metrics.get("spaces"):
                        metrics_file_count += 1
                        error = get_metrics(commit, metrics["spaces"])
                        if error:
                            logger.debug(
                                f"rust-code-analysis error on commit {commit.node}, path {path}"
                            )

                        touched_functions = get_touched_functions(
                            metrics["spaces"],
                            stats["deleted_lines"],
                            stats["added_lines"],
                        )
                        if len(touched_functions) > 0:
                            commit.functions[path] = list(touched_functions)

                    # Replace type with "Objective-C/C++" if rust-code-analysis detected this is an Objective-C/C++ file.
                    if type_ == "C/C++" and metrics.get("language") == "obj-c/c++":
                        type_ = "Objective-C/C++"

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
        commit.average_halstead_n2 = commit.total_halstead_n2 / metrics_file_count
        commit.average_halstead_N2 = commit.total_halstead_N2 / metrics_file_count
        commit.average_halstead_n1 = commit.total_halstead_n1 / metrics_file_count
        commit.average_halstead_N1 = commit.total_halstead_N1 / metrics_file_count
        commit.average_source_loc = commit.total_source_loc / metrics_file_count
        commit.average_logical_loc = commit.total_logical_loc / metrics_file_count
    else:
        # these values are initialized with sys.maxsize (because we take the min)
        # if no files, then reset them to 0 (it'd be stupid to have min > max)
        commit.minimum_cyclomatic = 0
        commit.minimum_halstead_N2 = 0
        commit.minimum_halstead_n2 = 0
        commit.minimum_halstead_N1 = 0
        commit.minimum_halstead_n1 = 0
        commit.minimum_source_loc = 0
        commit.minimum_logical_loc = 0

    return commit


def _transform(commit):
    return transform(HG, REPO_DIR, commit)


def hg_log(hg: hglib.client, revs: List[bytes]) -> List[Commit]:
    if len(revs) == 0:
        return []

    template = "{node}\\0{author}\\0{desc}\\0{bug}\\0{backedoutby}\\0{author|email}\\0{pushdate|hgdate}\\0{reviewers}\\0{backsoutnodes}\\0{rev}\\0"

    args = hglib.util.cmdbuilder(
        b"log", template=template, no_merges=True, rev=revs, branch="tip",
    )
    x = hg.rawcommand(args)
    out = x.split(b"\x00")[:-1]

    commits = []
    for rev in hglib.util.grouper(template.count("\\0"), out):
        assert b" " in rev[6]
        pushdate_timestamp = rev[6].split(b" ", 1)[0]
        if pushdate_timestamp != b"0":
            pushdate = datetime.utcfromtimestamp(float(pushdate_timestamp))
        else:
            pushdate = datetime.utcnow()

        bug_id = int(rev[3].decode("ascii")) if rev[3] else None

        reviewers = (
            list(set(sys.intern(r) for r in rev[7].decode("utf-8").split(" ")))
            if rev[7] != b""
            else []
        )

        backsout = (
            list(set(sys.intern(r) for r in rev[8].decode("utf-8").split(" ")))
            if rev[8] != b""
            else []
        )

        commits.append(
            Commit(
                revision=int(rev[9].decode("ascii")),
                node=sys.intern(rev[0].decode("ascii")),
                author=sys.intern(rev[1].decode("utf-8")),
                desc=rev[2].decode("utf-8"),
                pushdate=pushdate,
                bug_id=bug_id,
                backsout=backsout,
                backedoutby=rev[4].decode("ascii"),
                author_email=rev[5].decode("utf-8"),
                reviewers=reviewers,
            )
        )

    return commits


def _hg_log(revs: List[bytes]) -> List[Commit]:
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


def calculate_experiences(commits: Collection[Commit], save: bool = True) -> None:
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

    def get_key(exp_type: str, commit_type: str, item: str) -> str:
        return f"{exp_type}${commit_type}${item}"

    def get_experience(
        exp_type: str, commit_type: str, item: str, day: int, default: Union[int, Tuple]
    ) -> utils.ExpQueue:
        key = get_key(exp_type, commit_type, item)
        try:
            return experiences[key]
        except KeyError:
            queue = utils.ExpQueue(day, EXPERIENCE_TIMESPAN + 1, default)
            experiences[key] = queue
            return queue

    def update_experiences(
        experience_type: str, day: int, items: Collection[str]
    ) -> None:
        for commit_type in ("", "backout"):
            exp_queues = tuple(
                get_experience(experience_type, commit_type, item, day, 0)
                for item in items
            )
            total_exps = tuple(exp_queues[i][day] for i in range(len(items)))
            timespan_exps = tuple(
                exp - exp_queues[i][day - EXPERIENCE_TIMESPAN]
                for exp, i in zip(total_exps, range(len(items)))
            )

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
                for i in range(len(items)):
                    exp_queues[i][day] = total_exps[i] + 1

    def update_complex_experiences(
        experience_type: str, day: int, items: Collection[str]
    ) -> None:
        for commit_type in ("", "backout"):
            exp_queues = tuple(
                get_experience(experience_type, commit_type, item, day, tuple())
                for item in items
            )
            all_commit_lists = tuple(exp_queues[i][day] for i in range(len(items)))
            before_commit_lists = tuple(
                exp_queues[i][day - EXPERIENCE_TIMESPAN] for i in range(len(items))
            )
            timespan_commit_lists = tuple(
                commit_list[len(before_commit_list) :]
                for commit_list, before_commit_list in zip(
                    all_commit_lists, before_commit_lists
                )
            )

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
                for i in range(len(items)):
                    exp_queues[i][day] = all_commit_lists[i] + (commit.node,)

    # A "day" is defined as 150 commits.
    prev_day = 0
    prev_commit = None
    for i, commit in enumerate(tqdm(commits)):
        day = int(commit.revision / 150)
        assert day >= 0
        if prev_commit is not None:
            assert (
                day >= prev_day
            ), f"Commit {commit.node} with revision {commit.revision} should come after {prev_commit.node} with revision {prev_commit.revision}"
        prev_day = day
        prev_commit = commit

        # When a file is moved/copied, copy original experience values to the copied path.
        for orig, copied in commit.file_copies.items():
            for commit_type in ("", "backout"):
                orig_key = get_key("file", commit_type, orig)
                if orig_key in experiences:
                    experiences[get_key("file", commit_type, copied)] = copy.deepcopy(
                        experiences[orig_key]
                    )
                else:
                    logger.warning(
                        f"Experience missing for file {orig}, type '{commit_type}', on commit {commit.node}"
                    )

        if (
            not commit.ignored
            and len(commit.backsout) == 0
            and commit.bug_id is not None
        ):
            update_experiences("author", day, (commit.author,))
            update_experiences("reviewer", day, commit.reviewers)

            update_complex_experiences("file", day, commit.files)
            update_complex_experiences("directory", day, commit.directories)
            update_complex_experiences("component", day, commit.components)


def set_commits_to_ignore(hg: hglib.client, repo_dir: str, commits: List[Commit]):
    # Skip commits which are in .hg-annotate-ignore-revs or which have
    # 'ignore-this-changeset' in their description (mostly consisting of very
    # large and not meaningful formatting changes).
    ignore_revs_content = hg.cat(
        [os.path.join(repo_dir, ".hg-annotate-ignore-revs").encode("ascii")], rev=b"-1"
    ).decode("utf-8")
    ignore_revs = set(line[:40] for line in ignore_revs_content.splitlines())

    for commit in commits:
        commit.ignored = (
            commit.node in ignore_revs or "ignore-this-changeset" in commit.desc
        )


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
    revs_groups = [revs[i : i + CHUNK_SIZE] for i in range(0, REVS_COUNT, CHUNK_SIZE)]

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


def download_commits(
    repo_dir: str,
    rev_start: str = None,
    revs: List[bytes] = None,
    save: bool = True,
    use_single_process: bool = False,
    include_no_bug: bool = False,
    include_backouts: bool = False,
    include_ignored: bool = False,
) -> Tuple[CommitDict, ...]:
    assert revs is not None or rev_start is not None

    with hglib.open(repo_dir) as hg:
        if revs is None:
            revs = get_revs(hg, rev_start)

        if len(revs) == 0:
            logger.info("No commits to analyze")
            return tuple()

        logger.info(f"Mining {len(revs)} commits...")

        if not use_single_process:
            logger.info(f"Using {os.cpu_count()} processes...")
            commits = hg_log_multi(repo_dir, revs)
        else:
            commits = hg_log(hg, revs)

        if save or not os.path.exists("data/component_mapping.lmdb"):
            logger.info("Downloading file->component mapping...")
            download_component_mapping()

        set_commits_to_ignore(hg, repo_dir, commits)

        commits_num = len(commits)

        logger.info(f"Mining {commits_num} patches...")

        global code_analysis_server
        code_analysis_server = rust_code_analysis_server.RustCodeAnalysisServer()

        if not use_single_process:
            with concurrent.futures.ProcessPoolExecutor(
                initializer=_init_process, initargs=(repo_dir,)
            ) as executor:
                commits = executor.map(_transform, commits, chunksize=64)
                commits = tqdm(commits, total=commits_num)
                commits = tuple(commits)
        else:
            get_component_mapping()

            commits = tuple(transform(hg, repo_dir, c) for c in commits)

            close_component_mapping()

    code_analysis_server.terminate()

    calculate_experiences(commits, save)

    logger.info("Applying final commits filtering...")

    commits = tuple(commit.to_dict() for commit in commits)

    if save:
        db.append(COMMITS_DB, commits)

    return tuple(
        filter_commits(
            commits,
            include_no_bug=include_no_bug,
            include_backouts=include_backouts,
            include_ignored=include_ignored,
        )
    )


def clean(hg, repo_dir):
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


def _run_hg_cmd(repo_dir, cmd, *args, **kwargs):
    cmd = hglib.util.cmdbuilder(cmd, *args, **kwargs,)

    cmd.insert(0, hglib.HGPATH)

    subprocess.run(cmd, cwd=repo_dir, check=True)


def clone(
    repo_dir: str,
    url: str = "https://hg.mozilla.org/mozilla-central",
    update: bool = False,
) -> None:
    try:
        with hglib.open(repo_dir) as hg:
            clean(hg, repo_dir)

        # Remove pushlog DB to make sure it's regenerated.
        try:
            os.remove(os.path.join(repo_dir, ".hg", "pushlog2.db"))
        except FileNotFoundError:
            logger.info("pushlog database doesn't exist")

        # Pull, to make sure the pushlog is generated.
        with hglib.open(repo_dir) as hg:
            logger.info(f"Pulling {repo_dir}")
            hg.pull(update=update)
            logger.info(f"{repo_dir} pulled")

        return
    except hglib.error.ServerError as e:
        if "abort: repository" not in str(e) and "not found" not in str(e):
            raise

    _run_hg_cmd(
        None,
        "robustcheckout",
        url,
        repo_dir,
        purge=True,
        sharebase=repo_dir + "-shared",
        networkattempts=7,
        branch=b"tip",
        noupdate=not update,
    )

    logger.info(f"{repo_dir} cloned")


def pull(repo_dir: str, branch: str, revision: str) -> None:
    """Pull a revision from a branch of a remote repository into a local repository"""

    def do_pull() -> None:
        with hglib.open(repo_dir) as hg:
            hg.pull(
                source=f"https://hg.mozilla.org/{branch}/".encode("ascii"),
                rev=revision.encode("ascii"),
            )

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(3),
        reraise=True,
        after=tenacity.after_log(logger, logging.DEBUG),
    )
    def trigger_pull() -> None:
        p = multiprocessing.Process(target=do_pull)
        p.start()
        p.join(60 * 3)

        if p.is_alive():
            p.terminate()
            p.join()
            raise Exception(f"Timed out while pulling from {branch} after 3 minutes")

    trigger_pull()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("repository_dir", help="Path to the repository", action="store")
    parser.add_argument(
        "rev_start", help="Which revision to start with", action="store"
    )
    args = parser.parse_args()

    download_commits(args.repository_dir, args.rev_start, save=False)
