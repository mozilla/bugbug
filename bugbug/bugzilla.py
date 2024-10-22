# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import collections
import csv
import math
import re
from datetime import datetime
from logging import INFO, basicConfig, getLogger
from typing import Iterable, Iterator, NewType
from urllib.parse import urlencode

import tenacity
from dateutil.relativedelta import relativedelta
from libmozdata.bugzilla import Bugzilla, BugzillaProduct, Query
from tqdm import tqdm

from bugbug import db, utils

basicConfig(level=INFO)
logger = getLogger(__name__)

utils.setup_libmozdata()

BugDict = NewType("BugDict", dict)

BUGS_DB = "data/bugs.json"
db.register(
    BUGS_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_bugs.latest/artifacts/public/bugs.json.zst",
    10,
)

PRODUCTS = (
    "Cloud Services",
    "Core",
    "Data Platform and Tools",
    "DevTools",
    "Developer Infrastructure",
    "External Software Affecting Firefox",
    "Fenix",
    "Firefox",
    "Firefox Build System",
    "Firefox for iOS",
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
    "Web Compatibility",
    "WebExtensions",
)

ADDITIONAL_PRODUCTS = (
    "bugzilla.mozilla.org",
    "CA Program",
    "Calendar",
    "Chat Core",
    "MailNews Core",
    "SeaMonkey",
    "Thunderbird",
)

ATTACHMENT_INCLUDE_FIELDS = [
    "id",
    "flags",
    "is_patch",
    "content_type",
    "creation_time",
    "file_name",
]

COMMENT_INCLUDE_FIELDS = [
    "id",
    "count",
    "text",
    "creation_time",
    "tags",
    "creator",
]

PRODUCT_COMPONENT_CSV_REPORT_URL = "https://bugzilla.mozilla.org/report.cgi"

PHAB_REVISION_PATTERN = re.compile(r"phabricator-D([0-9]+)-url.txt")

MAINTENANCE_EFFECTIVENESS_SEVERITY_WEIGHTS = {
    "--": 3,
    "S1": 8,
    "S2": 5,
    "S3": 2,
    "S4": 1,
}
MAINTENANCE_EFFECTIVENESS_SEVERITY_DEFAULT_WEIGHT = 3

INCLUDE_FIELDS = ["_default", "filed_via"]


def get_bugs(
    include_invalid: bool | None = False,
    include_additional_products: tuple[str, ...] = (),
) -> Iterator[BugDict]:
    products = (
        PRODUCTS + include_additional_products
        if include_additional_products
        else PRODUCTS
    )
    yield from (
        bug
        for bug in db.read(BUGS_DB)
        if bug["product"] in products
        and (include_invalid or bug["product"] != "Invalid Bugs")
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
        include_fields=INCLUDE_FIELDS,
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
        "product": PRODUCTS + ADDITIONAL_PRODUCTS,
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

    logger.info("Loaded %d bugs.", old_bug_count)

    new_bug_ids = sorted(list(new_bug_ids_set))

    chunks = (
        new_bug_ids[i : (i + Bugzilla.BUGZILLA_CHUNK_SIZE)]
        for i in range(0, len(new_bug_ids), Bugzilla.BUGZILLA_CHUNK_SIZE)
    )

    @tenacity.retry(
        stop=tenacity.stop_after_attempt(7),
        wait=tenacity.wait_exponential(multiplier=2, min=2),
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

    data = {}

    def handler(bug):
        data["bug_count"] = bug["bug_count"]

    Bugzilla(queries=Query(Bugzilla.API_URL, bug_query_params, handler)).wait()

    return data["bug_count"]


def fetch_components_list(product_types="accessible") -> list[tuple]:
    """Fetch all components from all products.

    Args:
        product_types: The types of products to fetch components from. Defaults
            to "accessible".

    Returns:
        A list of tuples where the first element is the product name and the
        second element is the component name.
    """
    components: list[tuple] = []

    def handler(product):
        components.extend(
            (product["name"], component["name"]) for component in product["components"]
        )

    BugzillaProduct(
        product_types=product_types,
        include_fields=["name", "components.name"],
        product_handler=handler,
    ).wait()

    return components


def get_product_component_count(months: int = 12) -> dict[str, int]:
    """Get the number of bugs per component.

    Returns:
        a dictionary where keys are full components (in the form of
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
        PRODUCT_COMPONENT_CSV_REPORT_URL,
        params=params,
        headers={
            "User-Agent": utils.get_user_agent(),
        },
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


def get_active_product_components(products=[]) -> set[tuple[str, str]]:
    active_components = set()

    def product_handler(product):
        if product["is_active"]:
            active_components.update(
                (product["name"], component["name"])
                for component in product["components"]
                if component["is_active"]
            )

    BugzillaProduct(
        product_names=products,
        product_types=["accessible"],
        include_fields=["name", "is_active", "components.name", "components.is_active"],
        product_handler=product_handler,
    ).wait()

    return active_components


def get_component_team_mapping() -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = collections.defaultdict(dict)

    def product_handler(product):
        for component in product["components"]:
            mapping[product["name"]][component["name"]] = component["team_name"]

    BugzillaProduct(
        product_types="accessible",
        include_fields=["name", "components.name", "components.team_name"],
        product_handler=product_handler,
    ).wait()

    return mapping


def get_groups_users(group_names: list[str]) -> list[str]:
    r = utils.get_session("bugzilla").get(
        "https://bugzilla.mozilla.org/rest/group",
        params={
            "names": group_names,
            "membership": "1",
        },
        headers={
            "X-Bugzilla-API-Key": Bugzilla.TOKEN,
            "User-Agent": utils.get_user_agent(),
        },
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
    teams: list[str],
    from_date: datetime,
    to_date: datetime,
    components: list[str] | None = None,
) -> dict[str, dict]:
    data: dict[str, dict[str, int]] = {
        "open": {},
        "opened": {},
        "closed": {},
    }

    logger.info(
        "Calculating maintenance effectiveness indicator for the %s teams from %s to %s",
        ", ".join(teams),
        from_date,
        to_date,
    )

    def build_query(severity: str | None, query_type: str):
        params: dict[str, int | str | list[str]] = {
            "bug_type": "defect",
            "team_name": teams,
        }

        if severity is not None and severity != "--":
            params["bug_severity"] = severity

        if components is not None:
            params["component"] = components

        if query_type in ("opened", "closed"):
            params.update(
                {
                    "chfieldfrom": from_date.strftime("%Y-%m-%d %H:%M:%S"),
                    "chfieldto": to_date.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )

        if query_type == "open":
            params.update(
                {
                    "f1": "resolution",
                    "o1": "equals",
                    "v1": "---",
                }
            )
        elif query_type == "opened":
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

        return params

    for severity in MAINTENANCE_EFFECTIVENESS_SEVERITY_WEIGHTS.keys():
        for query_type in data.keys():
            params = build_query(severity, query_type)

            r = utils.get_session("bugzilla").get(
                "https://bugzilla.mozilla.org/rest/bug",
                params={**params, "count_only": 1},
                headers={
                    "X-Bugzilla-API-Key": Bugzilla.TOKEN,
                    "User-Agent": utils.get_user_agent(),
                },
            )
            r.raise_for_status()

            data[query_type][severity] = r.json()["bug_count"]

    # Calculate number of bugs without severity set.
    for query_type in data.keys():
        data[query_type]["--"] = data[query_type]["--"] - sum(
            data[query_type][s]
            for s in MAINTENANCE_EFFECTIVENESS_SEVERITY_WEIGHTS.keys()
            if s != "--"
        )

    open_defects = sum(data["open"].values())
    opened_defects = sum(data["opened"].values())
    closed_defects = sum(data["closed"].values())

    print("Before applying weights:")
    print(data)

    for query_type in data.keys():
        # Apply weights.
        for (
            severity,
            weight,
        ) in MAINTENANCE_EFFECTIVENESS_SEVERITY_WEIGHTS.items():
            data[query_type][severity] *= weight

    print("After applying weights:")
    print(data)

    weighed_open_defects = sum(data["open"].values())
    weighed_opened_defects = sum(data["opened"].values())
    weighed_closed_defects = sum(data["closed"].values())

    if weighed_opened_defects > 0:
        mei = 100 * weighed_closed_defects / weighed_opened_defects
    else:
        mei = 100 * (weighed_closed_defects + 1)

    duration = (to_date - from_date).total_seconds() / 31536000

    if closed_defects > opened_defects:
        bdtime = duration * (open_defects / (closed_defects - opened_defects))
    else:
        bdtime = math.inf

    if weighed_closed_defects > weighed_opened_defects:
        wbdtime = duration * (
            weighed_open_defects / (weighed_closed_defects - weighed_opened_defects)
        )
    else:
        wbdtime = math.inf

    estimated_start_open_defects = open_defects + closed_defects - opened_defects
    if estimated_start_open_defects > 0:
        incoming = 100 * opened_defects / estimated_start_open_defects
        closed = 100 * closed_defects / estimated_start_open_defects
    else:
        incoming = math.inf
        closed = math.inf

    opened_query = build_query(None, "opened")
    closed_query = build_query(None, "closed")

    return {
        "stats": {
            "ME": mei,
            "BDTime": bdtime,
            "WBDTime": wbdtime,
            "Incoming vs total open": incoming,
            "Closed vs total open": closed,
        },
        "queries": {
            "Opened": f"https://bugzilla.mozilla.org/buglist.cgi?query_format=advanced&{urlencode(opened_query, doseq=True)}",
            "Closed": f"https://bugzilla.mozilla.org/buglist.cgi?query_format=advanced&{urlencode(closed_query, doseq=True)}",
        },
    }
