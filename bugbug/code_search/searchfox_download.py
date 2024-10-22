# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import os
import subprocess
import sys

from bugbug.utils import get_session, get_user_agent

SEARCHFOX_STORAGE_DATA = "searchfox_data"


class SearchfoxDataNotAvailable(Exception):
    pass


def fetch(commit_hash: str) -> str:
    folders = os.listdir(SEARCHFOX_STORAGE_DATA)
    for folder in folders:
        if folder.startswith(commit_hash):
            return os.path.join(SEARCHFOX_STORAGE_DATA, folder)

    # https://firefox-ci-tc.services.mozilla.com/tasks/index/gecko.v2.mozilla-central.pushdate.2023.06.01.20230601042516.firefox/linux64-searchfox-debug

    baseUrl = "https://firefox-ci-tc.services.mozilla.com/api/index/v1/task/gecko.v2.mozilla-central.commit_hash.%s.firefox.%s-searchfox-debug"
    artifactBaseUrl = "https://firefoxci.taskcluster-artifacts.net/%s/0/%s"

    # target_oses = ['linux64', 'win64', 'macosx64', 'android-armv7']
    target_oses = ["linux64"]

    session = get_session("searchfox")

    for index_os in target_oses:
        indexUrl = baseUrl % (commit_hash, index_os)
        if len(sys.argv) > 2:
            indexUrl = sys.argv[2]
        indexRequest = session.get(
            indexUrl,
            headers={
                "User-Agent": get_user_agent(),
            },
        )
        if not indexRequest.ok:
            raise SearchfoxDataNotAvailable("Searchfox task not indexed")

        indexEntry = indexRequest.json()
        taskId = indexEntry["taskId"]

        targetJsonUrl = artifactBaseUrl % (taskId, "public/build/target.json")
        targetJsonRequest = session.get(
            targetJsonUrl,
            headers={
                "User-Agent": get_user_agent(),
            },
        )
        if not targetJsonRequest.ok:
            raise SearchfoxDataNotAvailable("Searchfox artifact not present")

        targetJson = targetJsonRequest.json()

        rev = targetJson["moz_source_stamp"]

        targetZipUrl = artifactBaseUrl % (
            taskId,
            "public/build/target.mozsearch-index.zip",
        )
        targetZipBasename = "%s_%s" % (rev, index_os)

        targetZipRequest = session.get(targetZipUrl, stream=True)
        if not targetZipRequest.ok:
            raise SearchfoxDataNotAvailable("Searchfox data no longer available")

        zip_path = os.path.join(SEARCHFOX_STORAGE_DATA, targetZipBasename)

        os.makedirs(zip_path)

        with open(
            os.path.join(SEARCHFOX_STORAGE_DATA, targetZipBasename, "searchfox.zip"),
            "wb",
        ) as f:
            for chunk in targetZipRequest.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)

        # TODO: This uses unzip, we might want to use Python instead. However, the archive
        # is a highly-compressed 300 MB file, that needs to be decompressed efficiently.
        subprocess.check_call(["unzip", "searchfox.zip"], cwd=zip_path)

        # TODO: When using only specific parts of searchfox data, such as syntax data,
        # it can be beneficial to filter the remaining data out just once.

        # for (path, dirs, files) in os.walk(SEARCHFOX_STORAGE_DATA):
        #    for file in files:
        #        fp_file = os.path.join(path, file)
        #        with open(fp_file, 'r') as fd:
        #            lines = fd.readlines()
        #        lines = [x for x in lines if '"syntax"' in x]
        #        with open(fp_file, 'w') as fd:
        #            fd.writelines(lines)

    folders = os.listdir(SEARCHFOX_STORAGE_DATA)
    for folder in folders:
        if folder.startswith(commit_hash):
            return os.path.join(SEARCHFOX_STORAGE_DATA, folder)

    assert False


if __name__ == "__main__":
    fetch(sys.argv[1])
