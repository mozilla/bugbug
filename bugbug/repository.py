# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import json
import multiprocessing
import os
import subprocess
from collections import namedtuple
from datetime import datetime

import hglib
from dateutil.relativedelta import relativedelta
from parsepatch.patch import Patch
from tqdm import tqdm

from bugbug import db

COMMITS_DB = 'data/commits.json'
db.register(COMMITS_DB, 'https://www.dropbox.com/s/mz3afgncx0siijc/commits.json.xz?dl=1')

COMPONENTS = {}

Commit = namedtuple('Commit', ['node', 'author', 'desc', 'date', 'bug', 'ever_backedout'])

author_experience = {}


def get_commits():
    return db.read(COMMITS_DB)


def _init(repo_dir):
    global HG
    HG = hglib.open(repo_dir)


def _transform(commit):
    desc = commit.desc.decode('utf-8')

    obj = {
        'author': commit.author.decode('utf-8'),
        'desc': desc,
        'date': str(commit.date),
        'bug_id': commit.bug.decode('utf-8'),
        'ever_backedout': commit.ever_backedout,
        'added': 0,
        'deleted': 0,
        'files_modified_num': 0,
        'types': set(),
        'components': list(),
        'author_experience': author_experience[commit]
    }

    patch = HG.export(revs=[commit.node], git=True)
    patch_data = Patch.parse_patch(patch.decode('utf-8', 'ignore'), skip_comments=False, add_lines_for_new=True)
    for path, stats in patch_data.items():
        if 'added' not in stats:
            # Must be a binary file
            obj['types'].add('binary')
            continue

        obj['added'] += len(stats['added']) + len(stats['touched'])
        obj['deleted'] += len(stats['deleted']) + len(stats['touched'])
        ext = os.path.splitext(path)[1]
        if ext in ['.js', '.jsm']:
            type_ = 'JavaScript'
        elif ext in ['.c', '.cpp', '.h']:
            type_ = 'C/C++'
        elif ext in ['.java']:
            type_ = 'Java'
        elif ext in ['.py']:
            type_ = 'Python'
        else:
            type_ = ext
        obj['types'].add(type_)

    obj['files_modified_num'] = len(patch_data)

    # Covert to a list, as a set is not JSON-serializable.
    obj['types'] = list(obj['types'])

    obj['components'] = list(set('::'.join(COMPONENTS[fl]) for fl in patch_data.keys() if COMPONENTS.get(fl)))

    return obj


def hg_log(hg, first_rev):
    template = '{node}\\0{author}\\0{desc}\\0{date}\\0{bug}\\0{backedoutby}\\0'

    args = hglib.util.cmdbuilder(b'log', template=template, no_merges=True, rev=f'{first_rev}:tip')
    x = hg.rawcommand(args)
    out = x.split(b'\x00')[:-1]

    revs = []
    for rev in hglib.util.grouper(template.count('\\0'), out):
        posixtime = float(rev[3].split(b'.', 1)[0])
        dt = datetime.fromtimestamp(posixtime)

        revs.append(Commit(
            node=rev[0],
            author=rev[1],
            desc=rev[2],
            date=dt,
            bug=rev[4],
            ever_backedout=(rev[5] != b''),
        ))

    return revs


def get_rev(hg, date):
    return hg.log(date=date.strftime('%Y-%m-%d'), limit=1)[0].node.decode('utf-8')


def download_commits(repo_dir, date_from):
    hg = hglib.open(repo_dir)

    first_rev = get_rev(hg, date_from)

    commits = hg_log(hg, first_rev)
    commits_num = len(commits)

    hg.close()

    commits_by_author = {}

    global author_experience
    for commit in commits:
        if commit.author not in commits_by_author:
            commits_by_author[commit.author] = 0
        else:
            commits_by_author[commit.author] += 1

        author_experience[commit] = commits_by_author[commit.author]

    subprocess.run([os.path.join(repo_dir, 'mach'), 'file-info', 'bugzilla-automation', 'component_data'], cwd=repo_dir, check=True)

    global COMPONENTS
    with open(os.path.join(repo_dir, 'component_data', 'components.json')) as cf:
        COMPONENTS = json.load(cf)

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
