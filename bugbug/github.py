# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import logging
from typing import Callable, Iterator, NewType

from ratelimit import limits, sleep_and_retry

from bugbug import db
from bugbug.utils import get_secret, get_session

logger = logging.getLogger(__name__)

IssueDict = NewType("IssueDict", dict)

DB_VERSION = 1
DB_URL = "https://community-tc.services.mozilla.com/api/index/v1/task/project.bugbug.data_github_{}_{}_issues.latest/artifacts/public/github_{}_{}_issues.json.zst"

PER_PAGE = 100
# Rate limit period in seconds
RATE_LIMIT_PERIOD = 900


class Github:
    def __init__(
        self, owner: str, repo: str, state: str = "all", retrieve_events: bool = False
    ) -> None:
        self.owner = owner
        self.repo = repo
        self.state = state
        self.retrieve_events = retrieve_events

        self.db_path = "data/github_{}_{}_issues.json".format(self.owner, self.repo)

        if not db.is_registered(self.db_path):
            db.register(
                self.db_path,
                DB_URL.format(self.owner, self.repo, self.owner, self.repo),
                DB_VERSION,
            )

    def get_issues(self) -> Iterator[IssueDict]:
        yield from db.read(self.db_path)

    def delete_issues(self, match: Callable[[IssueDict], bool]) -> None:
        db.delete(self.db_path, match)

    @sleep_and_retry
    @limits(calls=1200, period=RATE_LIMIT_PERIOD)
    def api_limit(self):
        # Allow a limited number of requests to account for rate limiting
        pass

    def get_token(self) -> str:
        return get_secret("GITHUB_TOKEN")

    def fetch_events(self, events_url: str) -> list:
        self.api_limit()
        logger.info(f"Fetching {events_url}")
        headers = {"Authorization": "token {}".format(self.get_token())}
        response = get_session("github").get(events_url, headers=headers)
        response.raise_for_status()
        events_raw = response.json()
        return events_raw

    def fetch_issues(
        self, url: str, retrieve_events: bool, params: dict = None
    ) -> tuple[list[IssueDict], dict]:
        self.api_limit()
        headers = {"Authorization": "token {}".format(self.get_token())}
        response = get_session("github").get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()

        # If only one issue is requested, add it to a list
        if isinstance(data, dict):
            data = [data]

        logger.info(f"Fetching {url}")

        if retrieve_events:
            for item in data:
                events = self.fetch_events(item["events_url"])
                item.update({"events": events})

        return data, response.links

    def get_start_page(self) -> int:
        # Determine next page to fetch based on number of downloaded issues
        issues = self.get_issues()
        count = sum(1 for _ in issues)
        return int(count / PER_PAGE) + 1

    def fetch_issues_updated_since_timestamp(self, since: str) -> list[IssueDict]:
        # Fetches changed and new issues since a specified timestamp
        url = "https://api.github.com/repos/{}/{}/issues".format(self.owner, self.repo)

        params = {"state": self.state, "since": since, "per_page": PER_PAGE, "page": 1}

        data, response_links = self.fetch_issues(
            url=url, retrieve_events=self.retrieve_events, params=params
        )

        # Fetch next page
        while "next" in response_links.keys():
            next_page_data, response_links = self.fetch_issues(
                response_links["next"]["url"], self.retrieve_events
            )
            data += next_page_data

        logger.info("Done fetching updates")

        return data

    def download_issues(self) -> None:
        # Fetches all issues sorted by date of creation in ascending order
        url = "https://api.github.com/repos/{}/{}/issues".format(self.owner, self.repo)
        start_page = self.get_start_page()

        params = {
            "state": self.state,
            "sort": "created",
            "direction": "asc",
            "per_page": PER_PAGE,
            "page": start_page,
        }

        data, response_links = self.fetch_issues(
            url=url, retrieve_events=self.retrieve_events, params=params
        )

        db.append(self.db_path, data)
        # Fetch next page
        while "next" in response_links.keys():
            next_page_data, response_links = self.fetch_issues(
                response_links["next"]["url"], self.retrieve_events
            )
            db.append(self.db_path, next_page_data)

        logger.info("Done downloading")

    def fetch_issue_by_number(
        self, owner: str, repo: str, issue_number: int, retrieve_events: bool = False
    ) -> IssueDict:
        # Fetches an issue by id
        url = "https://api.github.com/repos/{}/{}/issues/{}".format(
            owner, repo, issue_number
        )

        data = self.fetch_issues(url=url, retrieve_events=retrieve_events)

        return data[0][0]
