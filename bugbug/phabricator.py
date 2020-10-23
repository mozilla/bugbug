# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from libmozdata.phabricator import PhabricatorAPI
from tqdm import tqdm

PHABRICATOR_API = None

TESTING_PROJECTS = {
    "PHID-PROJ-h7y4cs7m2o67iczw62pp": "testing-approved",
    "PHID-PROJ-e4fcjngxcws3egiecv3r": "testing-exception-elsewhere",
    "PHID-PROJ-iciyosoekrczpf2a4emw": "testing-exception-other",
    "PHID-PROJ-zjipshabawolpkllehvg": "testing-exception-ui",
    "PHID-PROJ-cspmf33ku3kjaqtuvs7g": "testing-exception-unchanged",
}


def set_api_key(url, api_key):
    global PHABRICATOR_API
    PHABRICATOR_API = PhabricatorAPI(api_key, url)


def get(rev_ids):
    assert PHABRICATOR_API is not None

    data = {}

    rev_ids = list(set(rev_ids))
    rev_ids_groups = (rev_ids[i : i + 100] for i in range(0, len(rev_ids), 100))

    with tqdm(total=len(rev_ids)) as progress_bar:
        for rev_ids_group in rev_ids_groups:
            out = PHABRICATOR_API.request(
                "differential.revision.search",
                constraints={
                    "ids": rev_ids_group,
                },
                attachments={"projects": True},
            )

            for result in out["data"]:
                data[result["id"]] = result

            progress_bar.update(len(rev_ids_group))

    return data


def get_testing_projects(rev):
    return [
        TESTING_PROJECTS[projectPHID]
        for projectPHID in rev["attachments"]["projects"]["projectPHIDs"]
        if projectPHID in TESTING_PROJECTS
    ]
