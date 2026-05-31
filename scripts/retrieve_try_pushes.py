# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import itertools
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from logging import INFO, basicConfig, getLogger

from tqdm import tqdm

from bugbug import db, utils

basicConfig(level=INFO)
logger = getLogger(__name__)


TRY_PUSHES_DB = "data/try_pushes.json"
db.register(
    TRY_PUSHES_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_try_pushes.latest/artifacts/public/try_pushes.json.zst",
    1,
)


def query_first_push_id_by_date(date):
    # https://sql.telemetry.mozilla.org/queries/119580/source
    # SELECT MIN(p.id) AS first_push_id
    # FROM push p
    # WHERE p.repository_id = '{{ repository_id }}'
    #   AND p.time >= '{{ startdate }}';
    results = utils.query_redash(
        119896,
        {
            "repository_id": 4,
            "startdate": date,
        },
    )
    return results[0]["first_push_id"]


def query_try_pushes(first_push_id, last_push_id):
    # https://sql.telemetry.mozilla.org/queries/119580/source
    # SELECT p.id,
    #        p.revision,
    #        jt.name AS job_name,
    #        j.result
    # FROM push p
    # JOIN job j ON j.push_id = p.id
    # JOIN job_type jt ON jt.id = j.job_type_id
    # WHERE p.repository_id = 4
    #     AND p.id >= {{ first_push_id }}
    #     AND p.id <= {{ last_push_id }}
    # ORDER BY p.id,
    #             jt.name,
    #             j.result;
    return utils.query_redash(
        119580,
        {
            "first_push_id": first_push_id,
            "last_push_id": last_push_id,
        },
    )


def get_try_pushes_and_jobs(last_processed_push_id):
    pushes = []

    # Treeherder stores 42 days of data for try.
    end = datetime.today() - timedelta(days=1)
    start = end - timedelta(days=42)

    first_push_id = query_first_push_id_by_date(start.strftime("%Y-%m-%d"))
    last_push_id = query_first_push_id_by_date(end.strftime("%Y-%m-%d"))

    if first_push_id <= last_processed_push_id:
        first_push_id = last_processed_push_id + 1

    logger.info(
        "Retrieving try pushes between %d and %d...", first_push_id, last_push_id
    )

    MAX_BATCH_SIZE = 210
    MIN_BATCH_SIZE = 1

    current = first_push_id

    with tqdm(total=last_push_id - first_push_id + 1) as pbar:
        while current <= last_push_id:
            batch_size = min(MAX_BATCH_SIZE, last_push_id - current + 1)

            while batch_size >= MIN_BATCH_SIZE:
                first = current
                last = min(current + batch_size - 1, last_push_id)

                try:
                    pushes += query_try_pushes(first, last)
                except Exception:
                    if batch_size == MIN_BATCH_SIZE:
                        raise

                    batch_size = max(MIN_BATCH_SIZE, batch_size // 2)
                    continue

                processed = last - first + 1
                current = last + 1
                pbar.update(processed)
                break

    return pushes


def main() -> None:
    db.download(TRY_PUSHES_DB)

    previous_pushes = {push["th_id"] for push in db.read(TRY_PUSHES_DB)}

    pushes_and_jobs = get_try_pushes_and_jobs(max(previous_pushes, default=0))

    pushes = {}
    for push_and_job in pushes_and_jobs:
        if push_and_job["id"] not in pushes:
            pushes[push_and_job["id"]] = {
                "th_id": push_and_job["id"],
                "revision": push_and_job["revision"],
                "tasks": [
                    {
                        "name": push_and_job["job_name"],
                        "result": push_and_job["result"],
                    }
                ],
            }
        else:
            pushes[push_and_job["id"]]["tasks"].append(
                {
                    "name": push_and_job["job_name"],
                    "result": push_and_job["result"],
                }
            )

    new_pushes = [
        push for push in pushes.values() if push["th_id"] not in previous_pushes
    ]

    def process_push(push):
        return {
            "th_id": push["th_id"],
            "try_data": utils.get_automationrelevance("try", push["revision"]),
            "tasks": push["tasks"],
        }

    def _track(it, pbar):
        for item in it:
            pbar.update(1)
            yield item

    BATCH_SIZE = 1000

    with tqdm(total=len(new_pushes)) as pbar:
        for batch in itertools.batched(new_pushes, BATCH_SIZE):
            with ThreadPoolExecutor() as executor:
                db.append(
                    TRY_PUSHES_DB,
                    _track(executor.map(process_push, batch), pbar),
                )

            utils.zstd_compress(TRY_PUSHES_DB)


if __name__ == "__main__":
    main()
