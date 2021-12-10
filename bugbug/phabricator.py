# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from datetime import datetime, timedelta
from typing import Collection, Iterator, NewType, Optional

import tenacity
from libmozdata.phabricator import PhabricatorAPI
from tqdm import tqdm

from bugbug import db
from bugbug.db import LastModifiedNotAvailable

logger = logging.getLogger(__name__)

RevisionDict = NewType("RevisionDict", dict)
TransactionDict = NewType("TransactionDict", dict)

REVISIONS_DB = "data/revisions.json"
db.register(
    REVISIONS_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_revisions.latest/artifacts/public/revisions.json.zst",
    4,
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


def get_transactions(rev_phid: str) -> Collection[TransactionDict]:
    assert PHABRICATOR_API is not None

    after = ""
    data = []

    while after is not None:
        out = tenacity.retry(
            wait=tenacity.wait_exponential(multiplier=1, min=4, max=64),
            stop=tenacity.stop_after_attempt(5),
        )(
            lambda PHABRICATOR_API=PHABRICATOR_API: PHABRICATOR_API.request(
                "transaction.search", objectIdentifier=rev_phid, limit=1000, after=after
            )
        )()
        data += out["data"]
        after = out["cursor"]["after"]

    return data


def get(
    rev_ids: Optional[Collection[int]] = None, modified_start: Optional[datetime] = None
) -> Collection[RevisionDict]:
    assert PHABRICATOR_API is not None

    assert (rev_ids is not None) ^ (modified_start is not None)
    constraints: dict = {}
    if rev_ids is not None:
        constraints["ids"] = rev_ids
        progress_bar = None
    elif modified_start is not None:
        constraints["modifiedStart"] = int(modified_start.timestamp())
        progress_bar = tqdm()

    after = ""
    data = []

    while after is not None:
        out = tenacity.retry(
            wait=tenacity.wait_exponential(multiplier=1, min=4, max=64),
            stop=tenacity.stop_after_attempt(5),
        )(
            lambda PHABRICATOR_API=PHABRICATOR_API: PHABRICATOR_API.request(
                "differential.revision.search",
                constraints=constraints,
                attachments={"projects": True, "reviewers": True},
                after=after,
            )
        )()
        data += out["data"]
        after = out["cursor"]["after"]

        if progress_bar is not None:
            progress_bar.update(100)

    if progress_bar is not None:
        progress_bar.close()
        data_iter = tqdm(data)
    else:
        data_iter = data

    for revision in data_iter:
        assert "transactions" not in revision
        revision["transactions"] = get_transactions(revision["phid"])

    if progress_bar is not None:
        data_iter.close()

    return data


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

    print(f"{len(new_rev_ids_list)} revisions left to download")

    with tqdm(total=len(new_rev_ids)) as progress_bar:
        for rev_ids_group in rev_ids_groups:
            revisions = get(rev_ids=rev_ids_group)

            progress_bar.update(len(rev_ids_group))

            db.append(REVISIONS_DB, revisions)


def download_modified_revisions():
    try:
        last_modified = db.last_modified(REVISIONS_DB)
    except LastModifiedNotAvailable:
        return

    modified_revisions = get(modified_start=last_modified)
    modified_revision_ids = set(rev["id"] for rev in modified_revisions)

    db.delete(REVISIONS_DB, lambda revision: revision["id"] in modified_revision_ids)

    db.append(REVISIONS_DB, modified_revisions)


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


def get_review_dates(
    rev: RevisionDict,
) -> tuple[Optional[datetime], list[datetime], list[datetime], list[datetime]]:
    creation_date = None
    review_dates = []

    exclusion_start_dates = []
    exclusion_end_dates = []

    for transaction in sorted(rev["transactions"], key=lambda t: t["dateCreated"]):
        if transaction["type"] == "create":
            assert creation_date is None
            creation_date = datetime.utcfromtimestamp(transaction["dateCreated"])

        if transaction["type"] in ("accept", "request-changes"):
            review_dates.append(datetime.utcfromtimestamp(transaction["dateCreated"]))

        if transaction["type"] in ("plan-changes", "close"):
            exclusion_start_dates.append(
                datetime.utcfromtimestamp(transaction["dateCreated"])
            )

        if transaction["type"] in ("request-review", "update", "reopen"):
            if len(exclusion_start_dates) == 0:
                continue

            exclusion_end_dates.append(
                datetime.utcfromtimestamp(transaction["dateCreated"])
            )

    return creation_date, review_dates, exclusion_start_dates, exclusion_end_dates


def get_first_review_time(rev: RevisionDict) -> Optional[timedelta]:
    (
        creation_date,
        review_dates,
        exclusion_start_dates,
        exclusion_end_dates,
    ) = get_review_dates(rev)

    if creation_date is None:
        logger.warning("Revision D{} has no creation date.".format(rev["id"]))
        return None

    if len(review_dates) == 0:
        return None

    first_review_date = min(review_dates)

    first_exclusion_start_date = min(exclusion_start_dates, default=None)
    first_exclusion_end_date = min(exclusion_end_dates, default=None)

    if (
        first_exclusion_start_date is not None
        and first_exclusion_end_date is not None
        and first_exclusion_start_date > first_exclusion_end_date
    ):
        logger.warning("Revision D{} was in an inconsistent state.".format(rev["id"]))

    if (
        first_exclusion_start_date is None
        or first_exclusion_start_date > first_review_date
    ):
        return first_review_date - creation_date
    elif first_exclusion_start_date is not None and (
        first_exclusion_end_date is None or first_exclusion_end_date > first_review_date
    ):
        logger.warning(
            "Revision D{} was accepted while in 'planned changes' or 'closed' state.".format(
                rev["id"]
            )
        )
        return first_review_date - creation_date
    else:
        return (
            first_review_date
            - creation_date
            - (first_exclusion_end_date - first_exclusion_start_date)
        )


def get_pending_review_time(rev: RevisionDict) -> Optional[timedelta]:
    if rev["fields"]["status"]["value"] != "needs-review":
        return None

    creation_date, _, exclusion_start_dates, exclusion_end_dates = get_review_dates(rev)

    if creation_date is None:
        logger.warning("Revision D{} has no creation date.".format(rev["id"]))
        return None

    last_exclusion_start_date = max(exclusion_start_dates, default=None)
    last_exclusion_end_date = max(exclusion_end_dates, default=None)

    if last_exclusion_start_date is not None and (
        last_exclusion_end_date is None
        or last_exclusion_start_date > last_exclusion_end_date
    ):
        logger.warning(
            "Revision D{} was in an inconsistent state (needs review, but is in an exception timespan).".format(
                rev["id"]
            )
        )

    if last_exclusion_end_date is not None:
        return datetime.utcnow() - last_exclusion_end_date
    else:
        return datetime.utcnow() - creation_date
