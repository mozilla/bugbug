# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from typing import Collection, Iterator, NewType, Optional

from libmozdata.phabricator import PhabricatorAPI
from tqdm import tqdm

from bugbug import db

logger = logging.getLogger(__name__)

RevisionDict = NewType("RevisionDict", dict)

REVISIONS_DB = "data/revisions.json"
db.register(
    REVISIONS_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_revisions.latest/artifacts/public/revisions.json.zst",
    1,
)

PHABRICATOR_API = None

TESTING_PROJECTS = {
    "PHID-PROJ-h7y4cs7m2o67iczw62pp": "testing-approved",
    "PHID-PROJ-e4fcjngxcws3egiecv3r": "testing-exception-elsewhere",
    "PHID-PROJ-iciyosoekrczpf2a4emw": "testing-exception-other",
    "PHID-PROJ-zjipshabawolpkllehvg": "testing-exception-ui",
    "PHID-PROJ-cspmf33ku3kjaqtuvs7g": "testing-exception-unchanged",
}


def get_revisions() -> Iterator[RevisionDict]:
    yield from db.read(REVISIONS_DB)


def set_api_key(url: str, api_key: str) -> None:
    global PHABRICATOR_API
    PHABRICATOR_API = PhabricatorAPI(api_key, url)


def get(rev_ids: Collection[int]) -> Collection[RevisionDict]:
    assert PHABRICATOR_API is not None

    out = PHABRICATOR_API.request(
        "differential.revision.search",
        constraints={
            "ids": rev_ids,
        },
        attachments={"projects": True},
    )

    return out["data"]


def download_revisions(rev_ids: Collection[int]) -> None:
    old_rev_count = 0
    new_rev_ids = set(int(rev_id) for rev_id in rev_ids)
    for rev in get_revisions():
        old_rev_count += 1
        if rev["id"] in new_rev_ids:
            new_rev_ids.remove(rev["id"])

    print(f"Loaded {old_rev_count} revisions.")

    new_rev_ids_list = sorted(list(new_rev_ids))
    rev_ids_groups = (
        new_rev_ids_list[i : i + 100] for i in range(0, len(new_rev_ids_list), 100)
    )

    with tqdm(total=len(new_rev_ids)) as progress_bar:
        for rev_ids_group in rev_ids_groups:
            revisions = get(rev_ids_group)

            progress_bar.update(len(rev_ids_group))

            db.append(REVISIONS_DB, revisions)


def get_testing_project(rev: RevisionDict) -> Optional[str]:
    testing_projects = [
        TESTING_PROJECTS[projectPHID]
        for projectPHID in rev["attachments"]["projects"]["projectPHIDs"]
        if projectPHID in TESTING_PROJECTS
    ]

    if len(testing_projects) > 1:
        logger.warning("Revision D{} has more than one testing tag.".format(rev["id"]))

    if len(testing_projects) == 0:
        return None

    return testing_projects[-1]
