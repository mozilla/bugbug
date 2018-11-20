# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import re

import hglib

from bugbug import db

COMMITS_DB = 'data/commits.json'
db.register(COMMITS_DB, 'https://www.dropbox.com/s/mz3afgncx0siijc/commits.json.xz?dl=1')


def get_commits():
    return db.read(COMMITS_DB)


def download_commits(repo_dir):
    hg = hglib.open(repo_dir)

    commits = hg.log()

    bug_pattern = re.compile('[\t ]*[Bb][Uu][Gg][\t ]*([0-9]+)')

    def transform(commit):
        desc = commit[5].decode('utf-8')

        bug_id = None
        bug_id_match = re.search(bug_pattern, desc)
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

    commits = [transform(commit) for commit in reversed(commits)]

    db.write(COMMITS_DB, commits)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('repository_dir', help='Path to the repository', action='store')
    args = parser.parse_args()

    download_commits(args.repository_dir)
