# -*- coding: utf-8 -*-

import argparse
import lzma
import os
import shutil
from datetime import datetime
from logging import getLogger, basicConfig, INFO

import hglib
from bugbug import bug_snapshot
from bugbug import bugzilla
from bugbug import labels
from bugbug import repository
from dateutil.relativedelta import relativedelta

basicConfig(level=INFO)
logger = getLogger(__name__)


def get_secret(secret_id):
    """ Return the secret value

    TODO: Support task-cluster secret API
    """
    env_variable_name = f"BUGBUG_{secret_id}"

    return os.environ[env_variable_name]



class Retriever(object):
    def __init__(self, cache_root):
        self.cache_root = cache_root

        assert os.path.isdir(cache_root), f'Cache root {cache_root} is not a dir.'
        self.repo_dir = os.path.join(cache_root, 'mozilla-central')

    def retrieve_commits(self):
        shared_dir = self.repo_dir + '-shared'
        cmd = hglib.util.cmdbuilder('robustcheckout',
                                    'https://hg.mozilla.org/mozilla-central',
                                    self.repo_dir,
                                    purge=True,
                                    sharebase=shared_dir,
                                    networkattempts=7,
                                    branch=b'tip')

        cmd.insert(0, hglib.HGPATH)

        proc = hglib.util.popen(cmd)
        out, err = proc.communicate()
        if proc.returncode:
            raise hglib.error.CommandError(cmd, proc.returncode, out, err)

        logger.info('mozilla-central cloned')

        two_years_and_six_months_ago = datetime.utcnow() - relativedelta(years=2, months=6)
        repository.download_commits(self.repo_dir, two_years_and_six_months_ago)

        logger.info('commit data extracted from repository')

        self.compress_file('data/commits.json')

    def retrieve_bugs(self):
        bugzilla.set_token(get_secret("BUGZILLA_TOKEN"))

        six_months_ago = datetime.utcnow() - relativedelta(months=6)
        two_years_and_six_months_ago = six_months_ago - relativedelta(months=1)
        logger.info('Downloading bugs from {} to {}'.format(two_years_and_six_months_ago, six_months_ago))
        bugzilla.download_bugs_between(two_years_and_six_months_ago, six_months_ago)

        logger.info('Downloading labelled bugs')
        bug_ids = labels.get_all_bug_ids()
        bugzilla.download_bugs(bug_ids)

        # Try to re-download inconsistent bugs, up to three times.
        for i in range(3):
            bug_ids = bug_snapshot.get_inconsistencies()
            if len(bug_ids) == 0:
                break

            logger.info(f'Re-downloading {len(bug_ids)} bugs, as they were inconsistent')
            bugzilla.delete_bugs(bug_ids)
            bugzilla.download_bugs(bug_ids)

        self.compress_file('data/bugs.json')

    def compress_file(self, path):
        with open(path, 'rb') as input_f:
            with lzma.open(f'{path}.xz', 'wb') as output_f:
                shutil.copyfileobj(input_f, output_f)


def main_retrieve_commits():
    description = "Retrieve and extract the information from Mozilla-Central repository"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("cache-root", help="Cache for repository clones.")

    args = parser.parse_args()

    retriever = Retriever(getattr(args, 'cache-root'))

    retriever.retrieve_commits()


def main_retrieve_bugs():
    description = "Retrieve and extract the information from Bugzilla instance"
    parser = argparse.ArgumentParser(description=description)

    parser.add_argument("cache-root", help="Cache for repository clones.")

    args = parser.parse_args()

    retriever = Retriever(getattr(args, 'cache-root'))
    retriever.retrieve_bugs()
