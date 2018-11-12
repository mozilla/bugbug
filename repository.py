# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse

import hglib

import db

COMMITS_DB = 'data/commits.json'


def get_commits():
    return db.read(COMMITS_DB)


def download_commits(repo_dir):
    hg = hglib.open(repo_dir)

    commits = hg.log()

    def transform(commit):
        return {
            'rev': commit[0].decode('utf-8'),
            'node': commit[1].decode('utf-8'),
            'tags': commit[2].decode('utf-8'),
            'branch': commit[3].decode('utf-8'),
            'author': commit[4].decode('utf-8'),
            'desc': commit[5].decode('utf-8'),
            'date': str(commit[6]),
        }

    commits = [transform(commit) for commit in commits]

    db.write(COMMITS_DB, commits)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('repository_dir', help='Path to the repository', action='store')
    args = parser.parse_args()

    download_commits(args.repository_dir)
