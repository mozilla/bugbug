# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re
from collections import defaultdict
from datetime import datetime, timezone

import pandas as pd
from libmozdata import versions
from sklearn.base import BaseEstimator, TransformerMixin

from bugbug import bug_snapshot, repository


def field(bug, field):
    if field in bug and bug[field] != "---":
        return bug[field]

    return None


class has_str(object):
    def __call__(self, bug, **kwargs):
        return field(bug, "cf_has_str")


class has_regression_range(object):
    def __call__(self, bug, **kwargs):
        return field(bug, "cf_has_regression_range")


class has_crash_signature(object):
    def __call__(self, bug, **kwargs):
        return "cf_crash_signature" in bug and bug["cf_crash_signature"] != ""


class keywords(object):
    def __init__(self, to_ignore=set()):
        self.to_ignore = to_ignore

    def __call__(self, bug, **kwargs):
        keywords = []
        subkeywords = []
        for keyword in bug["keywords"]:
            if keyword in self.to_ignore:
                continue

            keywords.append(keyword)

            if keyword.startswith("sec-"):
                subkeywords.append("sec-")
            elif keyword.startswith("csectype-"):
                subkeywords.append("csectype-")
        return keywords + subkeywords


class severity(object):
    def __call__(self, bug, **kwargs):
        return field(bug, "severity")


class number_of_bug_dependencies(object):
    def __call__(self, bug, **kwargs):
        return len(bug["depends_on"])


class is_coverity_issue(object):
    def __call__(self, bug, **kwargs):
        return (
            re.search("[CID ?[0-9]+]", bug["summary"]) is not None
            or re.search("[CID ?[0-9]+]", bug["whiteboard"]) is not None
        )


class has_url(object):
    def __call__(self, bug, **kwargs):
        return bug["url"] != ""


class has_w3c_url(object):
    def __call__(self, bug, **kwargs):
        return "w3c" in bug["url"]


class has_github_url(object):
    def __call__(self, bug, **kwargs):
        return "github" in bug["url"]


class whiteboard(object):
    def __call__(self, bug, **kwargs):

        # Split by '['
        paren_splits = bug["whiteboard"].lower().split("[")

        # Split splits by space if they weren't in [ and ].
        splits = []
        for paren_split in paren_splits:
            if "]" in paren_split:
                paren_split = paren_split.split("]")
                splits += paren_split
            else:
                splits += paren_split.split(" ")

        # Remove empty splits and strip
        splits = [split.strip() for split in splits if split.strip() != ""]

        # For splits which contain ':', return both the whole string and the string before ':'.
        splits += [split.split(":", 1)[0] for split in splits if ":" in split]

        return splits


class patches(object):
    def __call__(self, bug, **kwargs):
        return sum(
            1
            for a in bug["attachments"]
            if a["is_patch"]
            or a["content_type"]
            in ["text/x-review-board-request", "text/x-phabricator-request"]
        )


class landings(object):
    def __call__(self, bug, **kwargs):
        return sum(1 for c in bug["comments"] if "://hg.mozilla.org/" in c["text"])


class title(object):
    def __call__(self, bug, **kwargs):
        ret = []

        keywords = ["fail"]
        for keyword in keywords:
            if keyword in bug["summary"].lower():
                ret.append(keyword)

        return ret


class product(object):
    def __call__(self, bug, **kwargs):
        return bug["product"]


class component(object):
    def __call__(self, bug, **kwargs):
        return bug["component"]


class is_mozillian(object):
    def __call__(self, bug, **kwargs):
        return any(
            bug["creator_detail"]["email"].endswith(domain)
            for domain in ["@mozilla.com", "@mozilla.org"]
        )


class bug_reporter(object):
    def __call__(self, bug, **kwargs):
        return bug["creator_detail"]["email"]


class delta_request_merge(object):
    def __call__(self, bug, **kwargs):
        for history in bug["history"]:
            for change in history["changes"]:
                if change["added"].startswith("approval-mozilla"):
                    uplift_request_datetime = datetime.strptime(
                        history["when"], "%Y-%m-%dT%H:%M:%SZ"
                    ).replace(tzinfo=timezone.utc)
                    timedelta = (
                        versions.getCloserRelease(uplift_request_datetime)[1]
                        - uplift_request_datetime
                    )
                    return timedelta.days + timedelta.seconds / (24 * 60 * 60)

        return None


class blocked_bugs_number(object):
    def __call__(self, bug, **kwargs):
        return len(bug["blocks"])


class priority(object):
    def __call__(self, bug, **kwargs):
        return bug["priority"]


class has_cve_in_alias(object):
    def __call__(self, bug, **kwargs):
        return bug["alias"] is not None and "CVE" in bug["alias"]


class comment_count(object):
    def __call__(self, bug, **kwargs):
        return field(bug, "comment_count")


class comment_length(object):
    def __call__(self, bug, **kwargs):
        return sum(len(x["text"]) for x in bug["comments"])


class reporter_experience(object):
    def __call__(self, bug, reporter_experience, **kwargs):
        return reporter_experience


class ever_affected(object):
    def __call__(self, bug, **kwargs):
        for history in bug["history"]:
            for change in history["changes"]:
                if (
                    change["field_name"].startswith("cf_status_firefox")
                    and change["added"] == "affected"
                ):
                    return True

        return False


class affected_then_unaffected(object):
    def __call__(self, bug, **kwargs):
        unaffected = []
        affected = []
        for key, value in bug.items():
            version = None
            if key.startswith("cf_status_firefox_esr"):
                version = key[len("cf_status_firefox_esr") :]
            elif key.startswith("cf_status_firefox"):
                version = key[len("cf_status_firefox") :]

            if version is None:
                continue

            if value == "unaffected":
                unaffected.append(version)
            elif value in [
                "affected",
                "fixed",
                "wontfix",
                "fix-optional",
                "verified",
                "disabled",
                "verified disabled",
            ]:
                affected.append(version)

        return any(
            unaffected_ver < affected_ver
            for unaffected_ver in unaffected
            for affected_ver in affected
        )


class commit_added(object):
    def __call__(self, bug, **kwargs):
        return sum(
            commit["added"] for commit in bug["commits"] if not commit["ever_backedout"]
        )


class commit_deleted(object):
    def __call__(self, bug, **kwargs):
        return sum(
            commit["deleted"]
            for commit in bug["commits"]
            if not commit["ever_backedout"]
        )


class commit_types(object):
    def __call__(self, bug, **kwargs):
        return sum(
            (
                commit["types"]
                for commit in bug["commits"]
                if not commit["ever_backedout"]
            ),
            [],
        )


class commit_files_modified_num(object):
    def __call__(self, bug, **kwargs):
        return sum(
            commit["files_modified_num"]
            for commit in bug["commits"]
            if not commit["ever_backedout"]
        )


class commit_author_experience(object):
    def __call__(self, bug, **kwargs):
        res = [
            commit["author_experience"]
            for commit in bug["commits"]
            if not commit["ever_backedout"]
        ]
        return sum(res) / len(res)


class commit_author_experience_90_days(object):
    def __call__(self, bug, **kwargs):
        res = [
            commit["author_experience_90_days"]
            for commit in bug["commits"]
            if not commit["ever_backedout"]
        ]
        return sum(res) / len(res)


class commit_reviewer_experience(object):
    def __call__(self, bug, **kwargs):
        res = [
            commit["reviewer_experience"]
            for commit in bug["commits"]
            if not commit["ever_backedout"]
        ]
        return sum(res) / len(res)


class commit_reviewer_experience_90_days(object):
    def __call__(self, bug, **kwargs):
        res = [
            commit["reviewer_experience_90_days"]
            for commit in bug["commits"]
            if not commit["ever_backedout"]
        ]
        return sum(res) / len(res)


class commit_no_of_backouts(object):
    def __call__(self, bug, **kwargs):
        return sum(1 for commit in bug["commits"] if commit["ever_backedout"])


class components_touched(object):
    def __call__(self, bug, **kwargs):
        return list(
            set(
                component
                for commit in bug["commits"]
                for component in commit["components"]
                if not commit["ever_backedout"]
            )
        )


class components_touched_num(object):
    def __call__(self, bug, **kwargs):
        return len(
            set(
                component
                for commit in bug["commits"]
                for component in commit["components"]
                if not commit["ever_backedout"]
            )
        )


class platform(object):
    def __call__(self, bug, **kwargs):
        return bug["platform"]


class op_sys(object):
    def __call__(self, bug, **kwargs):
        return bug["op_sys"]


class is_reporter_a_developer(object):
    def __call__(self, bug, author_ids, **kwargs):
        return bug_reporter()(bug).strip() in author_ids


class had_severity_enhancement(object):
    def __call__(self, bug, **kwargs):
        for history in bug["history"]:
            for change in history["changes"]:
                if (
                    change["field_name"] == "severity"
                    and change["added"] == "enhancement"
                ):
                    return True

        return False


def get_author_ids():
    author_ids = set()
    for commit in repository.get_commits():
        author_ids.add(commit["author_email"])
    return author_ids


class BugExtractor(BaseEstimator, TransformerMixin):
    def __init__(
        self,
        feature_extractors,
        cleanup_functions,
        rollback=False,
        rollback_when=None,
        commit_data=False,
    ):
        self.feature_extractors = feature_extractors
        self.cleanup_functions = cleanup_functions
        self.rollback = rollback
        self.rollback_when = rollback_when
        self.commit_map = repository.get_commit_map() if commit_data else None
        assert self.commit_map is None or len(self.commit_map) > 0

    def fit(self, x, y=None):
        return self

    def transform(self, bugs):
        results = []

        reporter_experience_map = defaultdict(int)
        author_ids = get_author_ids() if self.commit_map else None

        for bug in bugs:
            bug_id = bug["id"]

            if self.rollback:
                bug = bug_snapshot.rollback(bug, self.rollback_when)

            data = {}

            if self.commit_map is not None:
                if bug_id in self.commit_map:
                    bug["commits"] = self.commit_map[bug_id]
                else:
                    bug["commits"] = []

            for f in self.feature_extractors:
                res = f(
                    bug,
                    reporter_experience=reporter_experience_map[bug["creator"]],
                    author_ids=author_ids,
                )

                if res is None:
                    continue

                if isinstance(res, list):
                    for item in res:
                        data[f.__class__.__name__ + "-" + item] = "True"
                    continue

                if isinstance(res, bool):
                    res = str(res)

                data[f.__class__.__name__] = res

            reporter_experience_map[bug["creator"]] += 1

            # TODO: Try simply using all possible fields instead of extracting features manually.

            for cleanup_function in self.cleanup_functions:
                bug["summary"] = cleanup_function(bug["summary"])
                for c in bug["comments"]:
                    c["text"] = cleanup_function(c["text"])

            result = {
                "data": data,
                "title": bug["summary"],
                "first_comment": bug["comments"][0]["text"],
                "comments": " ".join([c["text"] for c in bug["comments"]]),
            }

            results.append(result)

        return pd.DataFrame(results)
