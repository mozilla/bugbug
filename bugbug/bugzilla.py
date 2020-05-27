# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import csv
from datetime import datetime
from typing import List, Set, Union

import tenacity
from dateutil.relativedelta import relativedelta
from libmozdata.bugzilla import Bugzilla
from tqdm import tqdm

from bugbug import db, utils

BUGS_DB = "data/bugs.json"
db.register(
    BUGS_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.relman.bugbug.data_bugs.latest/artifacts/public/bugs.json.zst",
    6,
)

PRODUCTS = (
    "Add-on SDK",
    "Android Background Services",
    "Core",
    "Core Graveyard",
    "DevTools",
    "DevTools Graveyard",
    "External Software Affecting Firefox",
    "Firefox",
    "Firefox Graveyard",
    "Firefox Build System",
    "Firefox for Android",
    "Firefox for Android Graveyard",
    # 'Firefox for iOS',
    "Firefox Health Report",
    # 'Focus',
    # 'Hello (Loop)',
    "Invalid Bugs",
    "NSPR",
    "NSS",
    "Toolkit",
    "Toolkit Graveyard",
    "WebExtensions",
)

ATTACHMENT_INCLUDE_FIELDS = [
    "id",
    "is_obsolete",
    "flags",
    "is_patch",
    "creator",
    "content_type",
    "creation_time",
]

COMMENT_INCLUDE_FIELDS = ["id", "count", "text", "author", "creation_time"]

PRODUCT_COMPONENT_CSV_REPORT_URL = "https://bugzilla.mozilla.org/report.cgi"


def get_bugs(include_invalid=False):
    yield from (
        bug
        for bug in db.read(BUGS_DB)
        if include_invalid or bug["product"] != "Invalid Bugs"
    )


def set_token(token):
    Bugzilla.TOKEN = token


def get_ids(params):
    assert "include_fields" not in params or params["include_fields"] == "id"

    old_CHUNK_SIZE = Bugzilla.BUGZILLA_CHUNK_SIZE
    try:
        Bugzilla.BUGZILLA_CHUNK_SIZE = 7000

        all_ids = []

        def bughandler(bug):
            all_ids.append(bug["id"])

        params["include_fields"] = "id"

        Bugzilla(params, bughandler=bughandler).get_data().wait()
    finally:
        Bugzilla.BUGZILLA_CHUNK_SIZE = old_CHUNK_SIZE

    return all_ids


def get(bug_ids: Union[List[int], Set[int]]):
    """Function to retrieve Bug Information including history, attachment, comments using Bugzilla REST API.

    :param bug_ids: find bug information for these `bug_ids`
    :type bug_ids: list of integers or str or int
    :return: dict with key as `id`(int) of a bug,  and values as bug_information
    :rtype: dict
    """

    def filter_keys(array_of_dict_values, required_fields):
        # This will mutates array_of_dict_values values in place
        for a_ind, a_value in enumerate(array_of_dict_values):
            a_value = {
                a_field: a_value[a_field]
                for a_field in list(a_value.keys())
                if a_field in required_fields
            }
            array_of_dict_values[a_ind] = a_value
        return array_of_dict_values

    def cleanup_fields_in_batch_of_bugs(batch_of_bugs):
        # Updation happens in place
        for a_bug_info_key in batch_of_bugs.keys():
            a_bug_info = batch_of_bugs[a_bug_info_key]
            current_comments_array = a_bug_info["comments"]
            current_comments_array = filter_keys(
                array_of_dict_values=current_comments_array,
                required_fields=COMMENT_INCLUDE_FIELDS,
            )
            a_bug_info["comments"] = current_comments_array
        return batch_of_bugs

    def attachmenthandler(bug, bug_id):
        bug_id = int(bug_id)
        if bug_id not in new_bugs:
            new_bugs[bug_id] = dict()
        new_bugs[bug_id]["attachments"] = bug

    bug_ids = sorted(set(bug_ids))

    new_bugs = dict()

    batch_size = Bugzilla.BUGZILLA_CHUNK_SIZE

    # Getting _default, history and comments information using REST API
    # as specified

    for i in range(0, len(bug_ids), batch_size):
        batch = bug_ids[i : i + batch_size]
        batch_of_ids = ",".join(map(str, batch))

        # "include_fields": "_default,history,comments,attachments",
        # Attachments data size is heavy, so handling them separately
        params_for_custom_fields = {
            "id": batch_of_ids,
            "include_fields": "_default,history,comments",
        }
        response = utils.get_session("bugzilla").get(
            "https://bugzilla.mozilla.org/rest/bug", params=params_for_custom_fields
        )
        response.raise_for_status()

        batch_of_bugs_info = response.json()
        batch_of_bugs_info = {
            int(a_bug["id"]): a_bug for a_bug in batch_of_bugs_info["bugs"]
        }
        batch_of_bugs_info = cleanup_fields_in_batch_of_bugs(batch_of_bugs_info)
        new_bugs.update(batch_of_bugs_info)

        Bugzilla(
            batch,
            bughandler=None,
            commenthandler=None,
            comment_include_fields=None,
            attachmenthandler=attachmenthandler,
            attachment_include_fields=ATTACHMENT_INCLUDE_FIELDS,
            historyhandler=None,
        ).get_data().wait()

    return new_bugs


def get_ids_between(date_from, date_to, security=False):
    params = {
        "f1": "creation_ts",
        "o1": "greaterthan",
        "v1": date_from.strftime("%Y-%m-%d"),
        "f2": "creation_ts",
        "o2": "lessthan",
        "v2": date_to.strftime("%Y-%m-%d"),
        "product": PRODUCTS,
    }

    if not security:
        params["f3"] = "bug_group"
        params["o3"] = "isempty"

    return get_ids(params)


def download_bugs(bug_ids, products=None, security=False):
    old_bug_count = 0
    new_bug_ids = set(int(bug_id) for bug_id in bug_ids)
    for bug in get_bugs(include_invalid=True):
        old_bug_count += 1
        if int(bug["id"]) in new_bug_ids:
            new_bug_ids.remove(bug["id"])

    print(f"Loaded {old_bug_count} bugs.")

    new_bug_ids = sorted(list(new_bug_ids))

    CHUNK_SIZE = 100

    chunks = (
        new_bug_ids[i : (i + CHUNK_SIZE)]
        for i in range(0, len(new_bug_ids), CHUNK_SIZE)
    )

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(7),
        wait=tenacity.wait_exponential(multiplier=1, min=16, max=64),
    )
    def get_chunk(chunk):
        new_bugs = get(chunk)

        if not security:
            new_bugs = [bug for bug in new_bugs.values() if len(bug["groups"]) == 0]

        if products is not None:
            new_bugs = [bug for bug in new_bugs.values() if bug["product"] in products]

        return new_bugs

    with tqdm(total=len(new_bug_ids)) as progress_bar:
        for chunk in chunks:
            new_bugs = get_chunk(chunk)

            progress_bar.update(len(chunk))

            db.append(BUGS_DB, new_bugs)


def delete_bugs(match):
    db.delete(BUGS_DB, match)


def count_bugs(bug_query_params):
    bug_query_params["count_only"] = 1

    r = utils.get_session("bugzilla").get(
        "https://bugzilla.mozilla.org/rest/bug", params=bug_query_params
    )
    r.raise_for_status()
    count = r.json()["bug_count"]

    return count


def get_product_component_csv_report():
    six_month_ago = datetime.utcnow() - relativedelta(months=6)

    # Base params
    url_params = {
        "f1": "creation_ts",
        "o1": "greaterthan",
        "v1": six_month_ago.strftime("%Y-%m-%d"),
        "x_axis_field": "product",
        "y_axis_field": "component",
        "action": "wrap",
        "ctype": "csv",
        "format": "table",
    }

    return PRODUCT_COMPONENT_CSV_REPORT_URL, url_params


def get_product_component_count():
    """ Returns a dictionary where keys are full components (in the form of
    `{product}::{component}`) and the value of the number of bugs for the
    given full components. Full component with 0 bugs are returned.
    """
    url, params = get_product_component_csv_report()
    csv_file = utils.get_session("bugzilla").get(url, params=params)
    csv_file.raise_for_status()
    content = csv_file.text

    csv_content = content.splitlines()
    component_key = "Component / Product"

    bugs_number = {}

    csv_reader = csv.DictReader(csv_content)
    for row in csv_reader:
        # Extract the component key
        component = row[component_key]

        for product, raw_value in row.items():
            if product == component_key:
                continue

            value = int(raw_value)

            full_comp = f"{product}::{component}"
            bugs_number[full_comp] = value

    return bugs_number
