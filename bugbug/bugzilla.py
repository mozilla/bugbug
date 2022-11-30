# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import csv
import re
from datetime import datetime
from typing import Iterable, Iterator, NewType, Optional

import tenacity
from dateutil.relativedelta import relativedelta
from libmozdata.bugzilla import Bugzilla
from tqdm import tqdm

from bugbug import db, utils

BugDict = NewType("BugDict", dict)

BUGS_DB = "data/bugs.json"
db.register(
    BUGS_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_bugs.latest/artifacts/public/bugs.json.zst",
    8,
)

PRODUCTS = (
    "Cloud Services",
    "Core",
    "Core Graveyard",
    "Data Platform and Tools",
    "DevTools",
    "DevTools Graveyard",
    "External Software Affecting Firefox",
    "Fenix",
    "Firefox",
    "Firefox Graveyard",
    "Firefox Build System",
    "GeckoView",
    "Invalid Bugs",
    "JSS",
    "NSPR",
    "NSS",
    "Release Engineering",
    "Remote Protocol",
    "Shield",
    "Testing",
    "Toolkit",
    "Toolkit Graveyard",
    "Web Compatibility",
    "WebExtensions",
)

ATTACHMENT_INCLUDE_FIELDS = [
    "id",
    "flags",
    "is_patch",
    "content_type",
    "creation_time",
    "file_name",
]

COMMENT_INCLUDE_FIELDS = ["id", "count", "text", "creation_time"]

PRODUCT_COMPONENT_CSV_REPORT_URL = "https://bugzilla.mozilla.org/report.cgi"

PHAB_REVISION_PATTERN = re.compile(r"phabricator-D([0-9]+)-url.txt")

MAINTENANCE_EFFECTIVENESS_SEVERITY_WEIGHTS = {
    "--": 5,
    "S1": 8,
    "S2": 5,
    "S3": 2,
    "S4": 1,
}
MAINTENANCE_EFFECTIVENESS_SEVERITY_DEFAULT_WEIGHT = 3


def get_bugs(include_invalid: Optional[bool] = False) -> Iterator[BugDict]:
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


def get(ids_or_query):
    new_bugs = {}

    def bughandler(bug):
        bug_id = int(bug["id"])

        if bug_id not in new_bugs:
            new_bugs[bug_id] = dict()

        new_bugs[bug_id].update(bug)

    def commenthandler(bug, bug_id):
        bug_id = int(bug_id)

        if bug_id not in new_bugs:
            new_bugs[bug_id] = dict()

        new_bugs[bug_id]["comments"] = bug["comments"]

    def attachmenthandler(bug, bug_id):
        bug_id = int(bug_id)

        if bug_id not in new_bugs:
            new_bugs[bug_id] = dict()

        new_bugs[bug_id]["attachments"] = bug

    def historyhandler(bug):
        bug_id = int(bug["id"])

        if bug_id not in new_bugs:
            new_bugs[bug_id] = dict()

        new_bugs[bug_id]["history"] = bug["history"]

    Bugzilla(
        ids_or_query,
        bughandler=bughandler,
        commenthandler=commenthandler,
        comment_include_fields=COMMENT_INCLUDE_FIELDS,
        attachmenthandler=attachmenthandler,
        attachment_include_fields=ATTACHMENT_INCLUDE_FIELDS,
        historyhandler=historyhandler,
    ).get_data().wait()

    return new_bugs


def get_ids_between(date_from, date_to=None, security=False, resolution=None):
    params = {
        "f1": "creation_ts",
        "o1": "greaterthan",
        "v1": date_from.strftime("%Y-%m-%d"),
        "product": PRODUCTS,
    }

    if date_to is not None:
        params["f2"] = "creation_ts"
        params["o2"] = "lessthan"
        params["v2"] = date_to.strftime("%Y-%m-%d")

    if not security:
        params["f3"] = "bug_group"
        params["o3"] = "isempty"

    if resolution is not None:
        params["resolution"] = resolution

    return get_ids(params)


def download_bugs(bug_ids: Iterable[int], security: bool = False) -> list[BugDict]:
    old_bug_count = 0
    new_bug_ids_set = set(int(bug_id) for bug_id in bug_ids)
    for bug in get_bugs(include_invalid=True):
        old_bug_count += 1
        new_bug_ids_set.discard(int(bug["id"]))

    print(f"Loaded {old_bug_count} bugs.")

    new_bug_ids = sorted(list(new_bug_ids_set))

    chunks = (
        new_bug_ids[i : (i + Bugzilla.BUGZILLA_CHUNK_SIZE)]
        for i in range(0, len(new_bug_ids), Bugzilla.BUGZILLA_CHUNK_SIZE)
    )

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(7),
        wait=tenacity.wait_exponential(multiplier=1, min=16, max=64),
    )
    def get_chunk(chunk: list[int]) -> list[BugDict]:
        new_bugs = get(chunk)

        if not security:
            new_bugs = [bug for bug in new_bugs.values() if len(bug["groups"]) == 0]

        return new_bugs

    all_new_bugs = []

    with tqdm(total=len(new_bug_ids)) as progress_bar:
        for chunk in chunks:
            new_bugs = get_chunk(chunk)

            progress_bar.update(len(chunk))

            db.append(BUGS_DB, new_bugs)

            all_new_bugs += new_bugs

    return all_new_bugs


def _find_linked(
    bug_map: dict[int, BugDict], bug: BugDict, link_type: str
) -> list[int]:
    return sum(
        (
            _find_linked(bug_map, bug_map[b], link_type)
            for b in bug[link_type]
            if b in bug_map
        ),
        [b for b in bug[link_type] if b in bug_map],
    )


def find_blocked_by(bug_map: dict[int, BugDict], bug: BugDict) -> list[int]:
    return _find_linked(bug_map, bug, "blocks")


def find_blocking(bug_map: dict[int, BugDict], bug: BugDict) -> list[int]:
    return _find_linked(bug_map, bug, "depends_on")


def get_fixed_versions(bug):
    versions = set()

    target_milestone_patterns = [
        re.compile("mozilla([0-9]+)"),
        re.compile("([0-9]+) Branch"),
        re.compile("Firefox ([0-9]+)"),
    ]
    for target_milestone_pattern in target_milestone_patterns:
        m = target_milestone_pattern.match(bug["target_milestone"])
        if m:
            versions.add(int(m.group(1)))

    status_pattern = re.compile("cf_status_firefox([0-9]+)")
    for field, value in bug.items():
        if value != "fixed":
            continue

        m = status_pattern.match(field)
        if m:
            versions.add(int(m.group(1)))

    return list(versions)


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


def get_product_component_count(months: int = 12) -> dict[str, int]:
    """Returns a dictionary where keys are full components (in the form of
    `{product}::{component}`) and the value of the number of bugs for the
    given full components. Full component with 0 bugs are returned.
    """
    since = datetime.utcnow() - relativedelta(months=months)

    # Base params
    params = {
        "f1": "creation_ts",
        "o1": "greaterthan",
        "v1": since.strftime("%Y-%m-%d"),
        "x_axis_field": "product",
        "y_axis_field": "component",
        "action": "wrap",
        "ctype": "csv",
        "format": "table",
    }

    csv_file = utils.get_session("bugzilla").get(
        PRODUCT_COMPONENT_CSV_REPORT_URL, params=params
    )
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
            # If there are no bugs, the product/component pair doesn't exist.
            if value == 0:
                continue

            full_comp = f"{product}::{component}"
            bugs_number[full_comp] = value

    return bugs_number


def get_component_team_mapping() -> dict[str, dict[str, str]]:
    r = utils.get_session("bugzilla").get(
        "https://bugzilla.mozilla.org/rest/product",
        params={
            "type": "accessible",
            "include_fields": ["name", "components.name", "components.team_name"],
        },
        headers={"X-Bugzilla-API-Key": Bugzilla.TOKEN, "User-Agent": "bugbug"},
    )
    r.raise_for_status()

    mapping: dict[str, dict[str, str]] = collections.defaultdict(dict)
    for product in r.json()["products"]:
        for component in product["components"]:
            mapping[product["name"]][component["name"]] = component["team_name"]

    return mapping


def get_groups_users(group_names: list[str]) -> list[str]:
    r = utils.get_session("bugzilla").get(
        "https://bugzilla.mozilla.org/rest/group",
        params={
            "names": group_names,
            "membership": "1",
        },
        headers={"X-Bugzilla-API-Key": Bugzilla.TOKEN, "User-Agent": "bugbug"},
    )
    r.raise_for_status()

    return [
        member["email"]
        for group in r.json()["groups"]
        for member in group["membership"]
    ]


def get_revision_ids(bug: BugDict) -> list[int]:
    revision_ids = []

    for attachment in bug["attachments"]:
        if attachment["content_type"] != "text/x-phabricator-request":
            continue

        match = PHAB_REVISION_PATTERN.search(attachment["file_name"])
        if match is None:
            continue

        revision_ids.append(int(match.group(1)))

    return revision_ids


def get_last_activity_excluding_bots(bug: BugDict) -> str:
    email_parts = [
        "@bots.tld",
        "@mozilla.tld",
        "nobody@mozilla.org",
    ]

    for history in bug["history"][::-1]:
        if not any(email_part in history["who"] for email_part in email_parts):
            return history["when"]

    return bug["creation_time"]


def calculate_maintenance_effectiveness_indicator(
    team,
    from_date,
    to_date,
    components=None,
):
    data: dict[str, dict[str, int]] = {
        "opened": {},
        "closed": {},
    }

    print(
        f"Calculating maintenance effectiveness indicator for the {team} team from {from_date} to {to_date}"
    )

    for severity in MAINTENANCE_EFFECTIVENESS_SEVERITY_WEIGHTS.keys():
        params = {
            "count_only": 1,
            "type": "defect",
            "team_name": team,
            "chfieldfrom": from_date.strftime("%Y-%m-%d"),
            "chfieldto": to_date.strftime("%Y-%m-%d"),
        }

        if severity != "--":
            params["bug_severity"] = severity

        if components is not None:
            params["component"] = components

        for query_type in ("opened", "closed"):
            if query_type == "opened":
                params["chfield"] = "[Bug creation]"
            elif query_type == "closed":
                params.update(
                    {
                        "chfield": "cf_last_resolved",
                        "f1": "resolution",
                        "o1": "notequals",
                        "v1": "---",
                    }
                )

            r = utils.get_session("bugzilla").get(
                "https://bugzilla.mozilla.org/rest/bug",
                params=params,
                headers={"X-Bugzilla-API-Key": Bugzilla.TOKEN, "User-Agent": "bugbug"},
            )
            r.raise_for_status()

            data[query_type][severity] = r.json()["bug_count"]

    # Calculate number of bugs without severity set.
    for query_type in ("opened", "closed"):
        data[query_type]["--"] = data[query_type]["--"] - sum(
            data[query_type][s]
            for s in MAINTENANCE_EFFECTIVENESS_SEVERITY_WEIGHTS.keys()
            if s != "--"
        )

    print("Before applying weights:")
    print(data)

    for query_type in ("opened", "closed"):
        # Apply weights.
        for (
            severity,
            weight,
        ) in MAINTENANCE_EFFECTIVENESS_SEVERITY_WEIGHTS.items():
            data[query_type][severity] *= weight

    print("After applying weights:")
    print(data)

    return (1 + sum(data["closed"].values())) / (1 + sum(data["opened"].values()))
