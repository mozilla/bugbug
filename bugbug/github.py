# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from typing import Callable, Iterator, List, NewType, Tuple

import requests
from ratelimit import limits, sleep_and_retry

from bugbug import db
from bugbug.utils import get_secret

logger = logging.getLogger(__name__)

GITHUB_ISSUES_DB = "data/github_issues.json"
db.register(
    GITHUB_ISSUES_DB,
    "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_github_issues.latest/artifacts/public/github_issues.json.zst",
    1,
)

IssueDict = NewType("IssueDict", dict)

PER_PAGE = 100
# Rate limit period in seconds
RATE_LIMIT_PERIOD = 900


def get_issues() -> Iterator[IssueDict]:
    yield from db.read(GITHUB_ISSUES_DB)


def delete_issues(match: Callable[[IssueDict], bool]) -> None:
    db.delete(GITHUB_ISSUES_DB, match)


@sleep_and_retry
@limits(calls=1200, period=RATE_LIMIT_PERIOD)
def api_limit():
    # Allow a limited number of requests to account for rate limiting
    pass


def get_token() -> str:
    return get_secret("GITHUB_TOKEN")


def fetch_events(events_url: str) -> list:
    api_limit()
    logger.info(f"Fetching {events_url}")
    headers = {"Authorization": "token {}".format(get_token())}
    response = requests.get(events_url, headers=headers)
    response.raise_for_status()
    events_raw = response.json()
    return events_raw


def fetch_issues(
    url: str, retrieve_events: bool, params: dict = None
) -> Tuple[List[IssueDict], dict]:
    api_limit()
    headers = {"Authorization": "token {}".format(get_token())}
    response = requests.get(url, params=params, headers=headers)
    response.raise_for_status()
    data = response.json()

    # If only one issue is requested, add it to a list
    if isinstance(data, dict):
        data = [data]

    logger.info(f"Fetching {url}")

    if retrieve_events:
        for item in data:
            events = fetch_events(item["events_url"])
            item.update({"events": events})

    return data, response.links


def get_start_page() -> int:
    # Determine next page to fetch based on number of downloaded issues
    issues = get_issues()
    count = sum(1 for _ in issues)
    return int(count / PER_PAGE) + 1


def fetch_issues_updated_since_timestamp(
    owner: str, repo: str, state: str, since: str, retrieve_events: bool = False
) -> List[IssueDict]:
    # Fetches changed and new issues since a specified timestamp
    url = "https://api.github.com/repos/{}/{}/issues".format(owner, repo)

    params = {"state": state, "since": since, "per_page": PER_PAGE, "page": 1}

    data, response_links = fetch_issues(
        url=url, retrieve_events=retrieve_events, params=params
    )

    # Fetch next page
    while "next" in response_links.keys():
        next_page_data, response_links = fetch_issues(
            response_links["next"]["url"], retrieve_events
        )
        data += next_page_data

    logger.info("Done fetching updates")

    return data


def download_issues(
    owner: str, repo: str, state: str, retrieve_events: bool = False
) -> None:
    # Fetches all issues sorted by date of creation in ascending order
    url = "https://api.github.com/repos/{}/{}/issues".format(owner, repo)
    start_page = get_start_page()

    params = {
        "state": state,
        "sort": "created",
        "direction": "asc",
        "per_page": PER_PAGE,
        "page": start_page,
    }

    data, response_links = fetch_issues(
        url=url, retrieve_events=retrieve_events, params=params
    )

    db.append(GITHUB_ISSUES_DB, data)
    # Fetch next page
    while "next" in response_links.keys():
        next_page_data, response_links = fetch_issues(
            response_links["next"]["url"], retrieve_events
        )
        db.append(GITHUB_ISSUES_DB, next_page_data)

    logger.info("Done downloading")


def fetch_issue_by_number(
    owner: str, repo: str, issue_number: int, retrieve_events: bool = False
) -> IssueDict:
    # Fetches an issue by id
    url = "https://api.github.com/repos/{}/{}/issues/{}".format(
        owner, repo, issue_number
    )

    data = fetch_issues(url=url, retrieve_events=retrieve_events)

    return data[0][0]
