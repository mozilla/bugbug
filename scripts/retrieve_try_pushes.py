# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

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


def get_try_pushes_and_jobs(last_push_id):
    pushes = []

    # Treeherder stores 42 days of data for try.
    end = yesterday = datetime.today() - timedelta(days=1)
    start = end - timedelta(days=42)

    while start < yesterday:
        end = min(start + timedelta(days=2), yesterday)
        logger.info("Retrieving 'try pushes' data between %s and %s...", start, end)

        """
        https://sql.telemetry.mozilla.org/queries/119580/source

        WITH pushes AS
        (SELECT DISTINCT ON (c.push_id) c.push_id AS id,
                            c.revision
        FROM push p
        JOIN COMMIT c ON c.push_id = p.id
        WHERE p.repository_id = 4
            AND p.time > '{{ startdate }}'
            AND p.time < CAST('{{ enddate }}' AS DATE) + INTERVAL '1' DAY
            AND p.id > COALESCE({{ last_push_id }}, 0)
        ORDER BY c.push_id,
                    c.id DESC)
        SELECT p.id,
            p.revision,
            jt.name AS job_name,
            j.result
        FROM pushes p
        JOIN job j ON j.push_id = p.id
        JOIN job_type jt ON j.job_type_id = jt.id
        ORDER BY p.id,
                jt.name,
                j.result;
        """
        pushes += utils.query_redash(
            119580,
            {
                "startdate": start.strftime("%Y-%m-%d"),
                "enddate": end.strftime("%Y-%m-%d"),
                "last_push_id": last_push_id,
            },
        )
        start = end

    return pushes


def main() -> None:
    db.download(TRY_PUSHES_DB)

    previous_pushes = {push["id"] for push in db.read(TRY_PUSHES_DB)}

    pushes_and_jobs = get_try_pushes_and_jobs(max(previous_pushes, default=0))

    pushes = {}
    for push_and_job in pushes_and_jobs:
        if push_and_job["id"] not in pushes:
            pushes[push_and_job["id"]] = {
                "id": push_and_job["id"],
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

    new_pushes = [push for push in pushes.values() if push["id"] not in previous_pushes]

    def process_push(push):
        return {
            "id": push["id"],
            "try_data": utils.get_automationrelevance("try", push["revision"]),
            "tasks": push["tasks"],
        }

    with ThreadPoolExecutor() as executor:
        results = tqdm(executor.map(process_push, new_pushes), total=len(new_pushes))
        db.append(TRY_PUSHES_DB, results)
    utils.zstd_compress(TRY_PUSHES_DB)


if __name__ == "__main__":
    main()
