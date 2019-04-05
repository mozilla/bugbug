# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import itertools
import multiprocessing
import os
from collections import defaultdict
from collections import namedtuple
from datetime import datetime

import hglib
import requests
from dateutil.relativedelta import relativedelta
from parsepatch.patch import Patch
from tqdm import tqdm

from bugbug import db

COMMITS_DB = 'data/commits.json'
db.register(COMMITS_DB, 'https://www.dropbox.com/s/mz3afgncx0siijc/commits.json.xz?dl=1', 'v1')

path_to_component = {}

Commit = namedtuple('Commit', ['node', 'author', 'desc', 'date', 'bug', 'backedoutby', 'author_email', 'files', 'file_copies'])

author_experience = {}
author_experience_90_days = {}

components_touched_prev = defaultdict(int)
components_touched_prev_90_days = defaultdict(int)


def get_commits():
    return db.read(COMMITS_DB)


def _init(repo_dir):
    global HG
    HG = hglib.open(repo_dir)


# This code was adapted from https://github.com/mozsearch/mozsearch/blob/2e24a308bf66b4c149683bfeb4ceeea3b250009a/router/router.py#L127
def is_test(path):
    return ('/test/' in path or '/tests/' in path or '/mochitest/' in path or '/unit/' in path or '/gtest/'
            in path or 'testing/' in path or '/jsapi-tests/' in path or '/reftests/' in path or '/reftest/'
            in path or '/crashtests/' in path or '/crashtest/' in path or '/gtests/' in path or '/googletest/' in path)


def _transform(commit):
    desc = commit.desc.decode('utf-8')

    obj = {
        'author': commit.author.decode('utf-8'),
        'desc': desc,
        'date': str(commit.date),
        'bug_id': int(commit.bug.decode('utf-8')) if commit.bug else None,
        'ever_backedout': commit.backedoutby != b'',
        'added': 0,
        'test_added': 0,
        'deleted': 0,
        'test_deleted': 0,
        'files_modified_num': 0,
        'types': set(),
        'components': list(),
        'author_experience': author_experience[commit.node],
        'author_experience_90_days': author_experience_90_days[commit.node],
        'author_email': commit.author_email.decode('utf-8'),
        'components_touched_prev': components_touched_prev[commit.node],
        'components_touched_prev_90_days': components_touched_prev_90_days[commit.node],
    }

    patch = HG.export(revs=[commit.node], git=True)
    patch_data = Patch.parse_patch(patch.decode('utf-8', 'ignore'), skip_comments=False, add_lines_for_new=True)
    for path, stats in patch_data.items():
        if 'added' not in stats:
            # Must be a binary file
            obj['types'].add('binary')
            continue

        if is_test(path):
            obj['test_added'] += len(stats['added']) + len(stats['touched'])
            obj['test_deleted'] += len(stats['deleted']) + len(stats['touched'])
        else:
            obj['added'] += len(stats['added']) + len(stats['touched'])
            obj['deleted'] += len(stats['deleted']) + len(stats['touched'])

        ext = os.path.splitext(path)[1]
        if ext in ['.js', '.jsm']:
            type_ = 'JavaScript'
        elif ext in ['.c', '.cpp', '.cc', '.cxx', '.m', '.mm', '.h', '.hh', '.hpp', '.hxx']:
            type_ = 'C/C++'
        elif ext == '.java':
            type_ = 'Java'
        elif ext == '.py':
            type_ = 'Python'
        elif ext == '.rs':
            type_ = 'Rust'
        else:
            type_ = ext
        obj['types'].add(type_)

    obj['files_modified_num'] = len(patch_data)

    # Covert to a list, as a set is not JSON-serializable.
    obj['types'] = list(obj['types'])

    obj['components'] = list(set(path_to_component[path] for path in patch_data.keys() if path_to_component.get(path)))

    return obj


def _hg_log(revs):
    template = '{node}\\0{author}\\0{desc}\\0{date}\\0{bug}\\0{backedoutby}\\0{author|email}\\0{join(files,"|")}\\0{join(file_copies,"|")}\\0'

    args = hglib.util.cmdbuilder(b'log', template=template, no_merges=True, rev=revs[0] + b':' + revs[-1], branch='central')
    x = HG.rawcommand(args)
    out = x.split(b'\x00')[:-1]

    revs = []
    for rev in hglib.util.grouper(template.count('\\0'), out):
        posixtime = float(rev[3].split(b'.', 1)[0])
        dt = datetime.fromtimestamp(posixtime)

        file_copies = {}
        for file_copy in rev[8].decode('utf-8').split('|'):
            if not file_copy:
                continue

            parts = file_copy.split(' (')
            copied = parts[0]
            orig = parts[1][:-1]
            file_copies[orig] = copied

        revs.append(Commit(
            node=rev[0],
            author=rev[1],
            desc=rev[2],
            date=dt,
            bug=rev[4],
            backedoutby=rev[5],
            author_email=rev[6],
            files=rev[7].decode('utf-8').split('|'),
            file_copies=file_copies,
        ))

    return revs


def get_revs(hg, date):
    revs = []

    # Since there are cases where on a given day there was no push, we have
    # to backtrack until we find a "good" day.
    while len(revs) == 0:
        rev_range = 'pushdate("{}"):tip'.format(date.strftime('%Y-%m-%d'))

        args = hglib.util.cmdbuilder(b'log', template='{node}\n', no_merges=True, rev=rev_range, branch='central')
        x = hg.rawcommand(args)
        revs = x.splitlines()

        date -= relativedelta(days=1)

    return revs


def download_commits(repo_dir, date_from):
    hg = hglib.open(repo_dir)

    revs = get_revs(hg, date_from)

    commits_num = len(revs)

    assert commits_num > 0, 'There should definitely be more than 0 commits, something is wrong'

    hg.close()

    processes = multiprocessing.cpu_count()

    print(f'Mining {commits_num} commits using {processes} processes...')

    CHUNK_SIZE = 256
    revs_groups = [revs[i:(i + CHUNK_SIZE)] for i in range(0, len(revs), CHUNK_SIZE)]

    with concurrent.futures.ProcessPoolExecutor(initializer=_init, initargs=(repo_dir,)) as executor:
        commits = executor.map(_hg_log, revs_groups, chunksize=20)
        commits = tqdm(commits, total=len(revs_groups))
        commits = list(itertools.chain.from_iterable(commits))

    # Don't analyze backouts.
    backouts = set(commit.backedoutby for commit in commits if commit.backedoutby != b'')
    commits = [commit for commit in commits if commit.node not in backouts]

    # Don't analyze commits that are not linked to a bug.
    commits = [commit for commit in commits if commit.bug != b'']

    # Skip commits which are in .hg-annotate-ignore-revs (mostly consisting of very
    # large and not meaningful formatting changes).
    with open(os.path.join(repo_dir, '.hg-annotate-ignore-revs'), 'r') as f:
        ignore_revs = set(l[:40].encode('utf-8') for l in f)

    commits = [commit for commit in commits if commit.node not in ignore_revs]

    commits_num = len(commits)

    print(f'Analyzing {commits_num} patches...')

    # Total previous number of commits by the author.
    total_commits_by_author = defaultdict(int)
    # Previous commits by the author, in a 90 days window.
    commits_by_author = defaultdict(list)

    global author_experience
    global author_experience_90_days
    for commit in commits:
        author_experience[commit.node] = total_commits_by_author[commit.author]
        # We don't want to consider backed out commits when calculating author/reviewer experience.
        if not commit.backedoutby:
            total_commits_by_author[commit.author] += 1

        # Keep only the previous commits from a window of 90 days in the commits_by_author map.
        cut = None

        for i, prev_commit in enumerate(commits_by_author[commit.author]):
            if (commit.date - prev_commit.date).days <= 90:
                break

            cut = i

        if cut is not None:
            commits_by_author[commit.author] = commits_by_author[commit.author][cut + 1:]

        author_experience_90_days[commit.node] = len(commits_by_author[commit.author])

        if not commit.backedoutby:
            commits_by_author[commit.author].append(commit)

    global path_to_component
    r = requests.get('https://index.taskcluster.net/v1/task/gecko.v2.mozilla-central.latest.source.source-bugzilla-info/artifacts/public/components.json')
    r.raise_for_status()
    path_to_component = r.json()
    path_to_component = {path: '::'.join(component) for path, component in path_to_component.items()}

    global components_touched_prev
    global components_touched_prev_90_days

    components_touched = defaultdict(int)
    prev_commits_90_days = []
    for commit in commits:
        components = set(path_to_component[path] for path in commit.files if path in path_to_component)

        for component in components:
            components_touched_prev[commit.node] += components_touched[component]

            components_touched[component] += 1

        if len(commit.file_copies) > 0:
            for orig, copied in commit.file_copies.items():
                if orig in path_to_component and copied in path_to_component:
                    components_touched[path_to_component[copied]] = components_touched[path_to_component[orig]]

        for i, prev_commit in enumerate(prev_commits_90_days):
            if (commit.date - prev_commit.date).days <= 90:
                break

            cut = i

        if cut is not None:
            prev_commits_90_days = prev_commits_90_days[cut + 1:]

        components_touched_90_days = defaultdict(int)
        for prev_commit in prev_commits_90_days:
            components_prev = set(path_to_component[path] for path in prev_commit.files if path in path_to_component)

            for component_prev in components_prev:
                components_touched_90_days[component_prev] += 1

            if len(prev_commit.file_copies) > 0:
                for orig, copied in prev_commit.file_copies.items():
                    if orig in path_to_component and copied in path_to_component:
                        components_touched_90_days[path_to_component[copied]] = components_touched_90_days[path_to_component[orig]]

        components_touched_prev_90_days[commit.node] = sum(components_touched_90_days[component] for component in components)
        prev_commits_90_days.append(commit)

    print(f'Mining commits using {multiprocessing.cpu_count()} processes...')

    with concurrent.futures.ProcessPoolExecutor(initializer=_init, initargs=(repo_dir,)) as executor:
        commits = executor.map(_transform, commits, chunksize=64)
        commits = tqdm(commits, total=commits_num)
        db.write(COMMITS_DB, commits)


def get_commit_map():
    commit_map = {}

    for commit in get_commits():
        bug_id = commit['bug_id']

        if not bug_id:
            continue

        if bug_id not in commit_map:
            commit_map[bug_id] = []

        commit_map[bug_id].append(commit)

    return commit_map


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('repository_dir', help='Path to the repository', action='store')
    args = parser.parse_args()

    two_years_and_six_months_ago = datetime.utcnow() - relativedelta(years=2, months=6)

    download_commits(args.repository_dir, two_years_and_six_months_ago)
