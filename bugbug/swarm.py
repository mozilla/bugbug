# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import json
import subprocess
from datetime import datetime
from typing import Collection


def api_revinfo(instance, rev_id):
    u = f"https://{instance}api/v10/reviews/{rev_id}"
    return u


def api_filelist_v_fromto(instance, rev_id, v1=0, v2=1):
    u = f"https://{instance}api/v10/reviews/{rev_id}/files?from={v1}&to={v2}"
    return u


def call(auth, g):
    command = f"curl -u \"{auth['user']}:{auth['password']}\" \"{g}\""
    process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return process.stdout


def p4_connect(auth):
    from P4 import P4  # from pip p4python

    p4 = P4()
    p4.port = auth["port"]
    p4.user = auth["user"]
    p4.password = auth["password"]
    p4.connect()
    p4.run_login()
    p4.tagged = False
    return p4


def get_review(instance, rev_id, version_num, auth):
    p4 = p4_connect(auth)
    data_rev = {}
    g = api_revinfo(instance, rev_id)
    message = json.loads(call(auth, g))
    data_rev = message["data"]

    fl = api_filelist_v_fromto(instance, rev_id, v1=version_num[0], v2=version_num[1])
    file_list = json.loads(call(auth, fl))
    for what in file_list["data"]:
        data_rev[what] = file_list["data"][what]

    commit_id = data_rev["reviews"][0]["versions"][version_num[1] - 1]["change"]

    diffs = {}
    for file in data_rev["files"]:
        filename1 = file["fromFile"] if "fromFile" in file else file["depotFile"]
        filename2 = file["depotFile"]
        commit_id1 = file["diffFrom"] if "diffFrom" in file else f"#{file['rev']}"
        commit_id2 = file["diffTo"] if "diffTo" in file else f"@={commit_id}"

        diffs[filename2] = "\n".join(
            p4.run(
                "diff2",
                "-u",
                "-du5",
                f"{filename1}{commit_id1}",
                f"{filename2}{commit_id2}",
            )
        )

    data_rev["diffs"] = diffs

    return data_rev


def get(
    AUTH,
    rev_ids: Collection[int] | None = None,
    modified_start: datetime | None = None,
    version_l=[0, 1],
):
    data = []
    instance = AUTH["instance"]
    if rev_ids is not None:
        for r in rev_ids:
            loc = get_review(instance, r, version_l, AUTH)

            full_diff = "".join([loc["diffs"][e] for e in loc["diffs"]])

            data += [
                {
                    "fields": {
                        "diffID": int(r),
                        "version": version_l,
                        "file_diff": loc["diffs"],
                        "diff": full_diff,
                    }
                }
            ]

    return data
