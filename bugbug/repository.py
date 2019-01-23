# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import concurrent.futures
import os
import re

import hglib
from parsepatch.patch import Patch

from bugbug import db

COMMITS_DB = 'data/commits.json'
db.register(COMMITS_DB, 'https://www.dropbox.com/s/mz3afgncx0siijc/commits.json.xz?dl=1')

BUG_PATTERN = re.compile('[\t ]*[Bb][Uu][Gg][\t ]*([0-9]+)')


def get_commits():
    return db.read(COMMITS_DB)


def _init(repo_dir):
    global HG
    HG = hglib.open(repo_dir)


def _transform(commit):
    desc = commit[5].decode('utf-8')

    bug_id = None
    bug_id_match = re.search(BUG_PATTERN, desc)
    if bug_id_match:
        bug_id = int(bug_id_match.group(1))

    return {
        # 'rev': commit[0].decode('utf-8'),
        # 'node': commit[1].decode('utf-8'),
        # 'tags': commit[2].decode('utf-8'),
        # 'branch': commit[3].decode('utf-8'),
        # 'author': commit[4].decode('utf-8'),
        'desc': desc,
        # 'date': str(commit[6]),
        'bug_id': bug_id,
    }


def download_commits(repo_dir):
    hg = hglib.open(repo_dir)

    commits = hg.log()

    hg.close()

    commits = (tuple(commit) for commit in commits)

    with concurrent.futures.ProcessPoolExecutor(initializer=_init, initargs=(repo_dir,)) as executor:
        commits = executor.map(_transform, commits, chunksize=256)
        db.write(COMMITS_DB, commits)


def get_commit_messages_map():
    commit_messages_map = {}

    for commit in get_commits():
        bug_id = commit['bug_id']

        if not bug_id:
            continue

        if bug_id not in commit_messages_map:
            commit_messages_map[bug_id] = ''

        commit_messages_map[bug_id] += commit['desc']

    return commit_messages_map


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('repository_dir', help='Path to the repository', action='store')
    args = parser.parse_args()

    download_commits(args.repository_dir)
