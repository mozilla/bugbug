# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from functools import partial
from multiprocessing.pool import Pool

import pandas as pd
from dateutil import parser
from libmozdata import versions
from libmozdata.bugzilla import Bugzilla
from sklearn.base import BaseEstimator, TransformerMixin

from bugbug import bug_snapshot, bugzilla, repository, utils

utils.setup_libmozdata()


def field(bug, field):
    if field in bug and bug[field] not in ("--", "---"):
        return bug[field]

    return None


class SingleBugFeature(object):
    pass


class HasSTR(SingleBugFeature):
    name = "Has STR"

    def __call__(self, bug, **kwargs):
        return field(bug, "cf_has_str")


class HasRegressionRange(SingleBugFeature):
    name = "Has Regression Range"

    def __call__(self, bug, **kwargs):
        return field(bug, "cf_has_regression_range")


class HasCrashSignature(SingleBugFeature):
    name = "Crash signature present"

    def __call__(self, bug, **kwargs):
        return "cf_crash_signature" in bug and bug["cf_crash_signature"] != ""


class Keywords(SingleBugFeature):
    def __init__(self, to_ignore=set(), prefixes_to_ignore=set()):
        self.to_ignore = to_ignore
        self.prefixes_to_ignore = prefixes_to_ignore

    def __call__(self, bug, **kwargs):
        keywords = []
        subkeywords = []
        for keyword in bug["keywords"]:
            if keyword in self.to_ignore or any(
                keyword.startswith(prefix) for prefix in self.prefixes_to_ignore
            ):
                continue

            keywords.append(keyword)

            if keyword.startswith("sec-"):
                subkeywords.append("sec-")
            elif keyword.startswith("csectype-"):
                subkeywords.append("csectype-")
        return keywords + subkeywords


class Severity(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return field(bug, "severity")


class NumberOfBugDependencies(SingleBugFeature):
    name = "# of bug dependencies"

    def __call__(self, bug, **kwargs):
        return len(bug["depends_on"])


class IsCoverityIssue(SingleBugFeature):
    name = "Is Coverity issue"

    def __call__(self, bug, **kwargs):
        return (
            re.search("[CID ?[0-9]+]", bug["summary"]) is not None
            or re.search("[CID ?[0-9]+]", bug["whiteboard"]) is not None
        )


class HasURL(SingleBugFeature):
    name = "Has a URL"

    def __call__(self, bug, **kwargs):
        return bug["url"] != ""


class HasW3CURL(SingleBugFeature):
    name = "Has a w3c URL"

    def __call__(self, bug, **kwargs):
        return "w3c" in bug["url"]


class HasGithubURL(SingleBugFeature):
    name = "Has a GitHub URL"

    def __call__(self, bug, **kwargs):
        return "github" in bug["url"]


def whiteboard_keywords(bug):
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


class Whiteboard(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return whiteboard_keywords(bug)


class Patches(SingleBugFeature):
    name = "# of patches"

    def __call__(self, bug, **kwargs):
        return sum(
            1
            for a in bug["attachments"]
            if a["is_patch"]
            or a["content_type"]
            in ["text/x-review-board-request", "text/x-phabricator-request"]
        )


class Landings(SingleBugFeature):
    name = "# of landing comments"

    def __call__(self, bug, **kwargs):
        return sum(1 for c in bug["comments"] if "://hg.mozilla.org/" in c["text"])


class Product(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return bug["product"]


class Component(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return bug["component"]


class IsMozillian(SingleBugFeature):
    name = "Reporter has a @mozilla email"

    def __call__(self, bug, **kwargs):
        return any(
            bug["creator_detail"]["email"].endswith(domain)
            for domain in ["@mozilla.com", "@mozilla.org"]
        )


class BugReporter(SingleBugFeature):
    name = "Bug reporter"

    def __call__(self, bug, **kwargs):
        return bug["creator_detail"]["email"]


class DeltaRequestMerge(SingleBugFeature):
    name = "Timespan between uplift request and following merge"

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


class DeltaNightlyRequestMerge(SingleBugFeature):
    name = "Time delta between landing of the patch in Nightly and uplift request"

    def __call__(self, bug, **kwargs):
        for history in bug["history"]:
            for change in history["changes"]:
                if not (
                    change["added"].startswith("approval-mozilla")
                    and change["added"].endswith("?")
                ):
                    continue

                uplift_request_datetime = parser.parse(history["when"])

                landing_comments = Bugzilla.get_landing_comments(
                    bug["comments"], ["nightly"]
                )

                # This will help us to find the closest landing before the uplift request
                landing_time_list = []
                for landing in landing_comments:
                    landing_time = parser.parse(landing["comment"]["creation_time"])

                    # Only accept if the uplift is on the future and
                    # if the landing_time is greater than the calculated now
                    if uplift_request_datetime >= landing_time:
                        landing_time_list.append(landing_time)

                if len(landing_time_list) > 0:
                    time_delta = uplift_request_datetime - max(landing_time_list)
                    return time_delta.days + time_delta.seconds / (24 * 60 * 60)
        return None


class BlockedBugsNumber(SingleBugFeature):
    name = "# of blocked bugs"

    def __call__(self, bug, **kwargs):
        return len(bug["blocks"])


class Priority(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return field(bug, "priority")


class Version(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        if bug["version"] in ("Default", "Trunk", "trunk"):
            return "Trunk"
        elif bug["version"] in ("other", "Other Branch"):
            return "other"
        elif bug["version"] == "unspecified":
            return None
        else:
            return "Has Value"


class TargetMilestone(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        if bug["target_milestone"] == "Future":
            return "Future"
        elif bug["target_milestone"] == "---":
            return None
        else:
            return "Has Value"


class HasCVEInAlias(SingleBugFeature):
    name = "CVE in alias"

    def __call__(self, bug, **kwargs):
        return bug["alias"] is not None and "CVE" in bug["alias"]


class CommentCount(SingleBugFeature):
    name = "# of comments"

    def __call__(self, bug, **kwargs):
        return field(bug, "comment_count")


class CommentLength(SingleBugFeature):
    name = "Length of comments"

    def __call__(self, bug, **kwargs):
        return sum(len(x["text"]) for x in bug["comments"])


class ReporterExperience(SingleBugFeature):
    name = "# of bugs previously opened by the reporter"

    def __call__(self, bug, reporter_experience, **kwargs):
        return reporter_experience


class EverAffected(SingleBugFeature):
    name = "status has ever been set to 'affected'"

    def __call__(self, bug, **kwargs):
        for history in bug["history"]:
            for change in history["changes"]:
                if (
                    change["field_name"].startswith("cf_status_firefox")
                    and change["added"] == "affected"
                ):
                    return True

        return False


def get_versions_statuses(bug):
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

    return unaffected, affected


class AffectedThenUnaffected(SingleBugFeature):
    name = "status has ever been set to 'affected' and 'unaffected'"

    def __call__(self, bug, **kwargs):
        unaffected, affected = get_versions_statuses(bug)
        return any(
            unaffected_ver < affected_ver
            for unaffected_ver in unaffected
            for affected_ver in affected
        )


class NumWordsTitle(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return len(bug["summary"].split())


class NumWordsComments(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return sum(len(comment["text"].split()) for comment in bug["comments"])


class HasAttachment(SingleBugFeature):
    name = "Attachment present"

    def __call__(self, bug, **kwargs):
        return len(bug["attachments"]) > 0


class HasImageAttachmentAtBugCreation(SingleBugFeature):
    name = "Image attachment present at bug creation"

    def __call__(self, bug, **kwargs):
        return any(
            "image" in attachment["content_type"]
            and attachment["creation_time"] == bug["creation_time"]
            for attachment in bug["attachments"]
        )


class HasImageAttachment(SingleBugFeature):
    name = "Image attachment present"

    def __call__(self, bug, **kwargs):
        return any(
            "image" in attachment["content_type"] for attachment in bug["attachments"]
        )


class CommitAdded(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return sum(
            commit["added"] for commit in bug["commits"] if not commit["backedoutby"]
        )


class CommitDeleted(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return sum(
            commit["deleted"] for commit in bug["commits"] if not commit["backedoutby"]
        )


class CommitTypes(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return sum(
            (commit["types"] for commit in bug["commits"] if not commit["backedoutby"]),
            [],
        )


class CommitFilesModifiedNum(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return sum(
            commit["files_modified_num"]
            for commit in bug["commits"]
            if not commit["backedoutby"]
        )


class CommitAuthorExperience(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        res = [
            commit["author_experience"]
            for commit in bug["commits"]
            if not commit["backedoutby"]
        ]
        return sum(res) / len(res)


class CommitAuthorExperience90Days(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        res = [
            commit["author_experience_90_days"]
            for commit in bug["commits"]
            if not commit["backedoutby"]
        ]
        return sum(res) / len(res)


class CommitReviewerExperience(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        res = [
            commit["reviewer_experience"]
            for commit in bug["commits"]
            if not commit["backedoutby"]
        ]
        return sum(res) / len(res)


class CommitReviewerExperience90Days(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        res = [
            commit["reviewer_experience_90_days"]
            for commit in bug["commits"]
            if not commit["backedoutby"]
        ]
        return sum(res) / len(res)


class CommitNoOfBackouts(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return sum(1 for commit in bug["commits"] if commit["backedoutby"])


class ComponentsTouched(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return list(
            set(
                component
                for commit in bug["commits"]
                for component in commit["components"]
                if not commit["backedoutby"]
            )
        )


class ComponentsTouchedNum(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return len(
            set(
                component
                for commit in bug["commits"]
                for component in commit["components"]
                if not commit["backedoutby"]
            )
        )


class Platform(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return bug["platform"]


class OpSys(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return bug["op_sys"]


class FiledVia(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return bug["filed_via"]


class IsReporterADeveloper(SingleBugFeature):
    def __call__(self, bug, author_ids, **kwargs):
        return BugReporter()(bug).strip() in author_ids


class HadSeverityEnhancement(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        for history in bug["history"]:
            for change in history["changes"]:
                if (
                    change["field_name"] == "severity"
                    and change["added"] == "enhancement"
                ):
                    return True

        return False


def get_time_to_fix(bug):
    if bug["resolution"] != "FIXED":
        return None

    if bug["cf_last_resolved"] is None:
        return None

    return (
        parser.parse(bug["cf_last_resolved"]) - parser.parse(bug["creation_time"])
    ).total_seconds() / 86400


class TimeToFix(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return get_time_to_fix(bug)


def get_time_to_assign(bug):
    for history in bug["history"]:
        for change in history["changes"]:
            if (
                change["field_name"] == "status"
                and change["removed"] in ("UNCONFIRMED", "NEW")
                and change["added"] == "ASSIGNED"
            ):
                return (
                    parser.parse(history["when"]) - parser.parse(bug["creation_time"])
                ).total_seconds() / 86400

    return None


class TimeToAssign(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return get_time_to_assign(bug)


def get_time_to_close(bug):
    """Calculate the time until closure or the time since closure for a bug."""
    if bug["cf_last_resolved"]:
        return (
            parser.parse(bug["cf_last_resolved"]) - parser.parse(bug["creation_time"])
        ).total_seconds() / 86400

    return (
        datetime.now(timezone.utc) - parser.parse(bug["creation_time"])
    ).total_seconds() / 86400


class TimeToClose(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return get_time_to_close(bug)


class CCNumber(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return len(bug["cc"])


class IsUplifted(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return any(
            change["added"].startswith("approval-mozilla")
            and change["added"].endswith("+")
            for history in bug["history"]
            for change in history["changes"]
        )


class Resolution(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return bug["resolution"]


class Status(SingleBugFeature):
    def __call__(self, bug, **kwargs):
        return bug["status"]


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
        merge_data=True,
    ):
        assert len(set(type(fe) for fe in feature_extractors)) == len(
            feature_extractors
        ), "Duplicate Feature Extractors"
        self.feature_extractors = feature_extractors

        assert len(set(type(cf) for cf in cleanup_functions)) == len(
            cleanup_functions
        ), "Duplicate Cleanup Functions"
        self.cleanup_functions = cleanup_functions
        self.rollback = rollback
        self.rollback_when = rollback_when
        self.commit_data = commit_data
        self.merge_data = merge_data

    def fit(self, x, y=None):
        for feature in self.feature_extractors:
            if hasattr(feature, "fit"):
                feature.fit(x())

        return self

    def transform(self, bugs):
        bugs_iter = iter(bugs())

        reporter_experience_map = defaultdict(int)
        author_ids = get_author_ids() if self.commit_data else None

        def apply_transform(bug):
            data = {}

            for feature_extractor in self.feature_extractors:
                res = feature_extractor(
                    bug,
                    reporter_experience=reporter_experience_map[bug["creator"]],
                    author_ids=author_ids,
                )

                if hasattr(feature_extractor, "name"):
                    feature_extractor_name = feature_extractor.name
                else:
                    feature_extractor_name = feature_extractor.__class__.__name__

                if res is None:
                    continue

                if isinstance(res, (list, set)):
                    for item in res:
                        data[sys.intern(f"{item} in {feature_extractor_name}")] = True
                    continue

                data[feature_extractor_name] = res

            reporter_experience_map[bug["creator"]] += 1

            summary = bug["summary"]
            comments = [c["text"] for c in bug["comments"]]
            for cleanup_function in self.cleanup_functions:
                summary = cleanup_function(summary)
                comments = [cleanup_function(comment) for comment in comments]

            return {
                "data": data,
                "title": summary,
                "first_comment": "" if len(comments) == 0 else comments[0],
                "comments": " ".join(comments),
            }

        def apply_rollback(bugs_iter):
            with Pool() as p:
                yield from p.imap(
                    partial(bug_snapshot.rollback, when=self.rollback_when),
                    bugs_iter,
                    chunksize=1024,
                )

        if self.rollback:
            bugs_iter = apply_rollback(bugs_iter)

        return pd.DataFrame(apply_transform(bug) for bug in bugs_iter)


class IsPerformanceBug(SingleBugFeature):
    """Determine if the bug is related to performance based on given bug data."""

    name = "Is Performance Bug"
    type_name = "performance"
    keyword_prefixes = ("perf", "topperf", "main-thread-io")
    whiteboard_prefixes = (
        "[fxperf",
        "[fxperfsize",
        "[snappy",
        "[pdfjs-c-performance",
        "[pdfjs-performance",
        "[sp3",
    )

    def __call__(
        self,
        bug: bugzilla.BugDict,
        bug_map: dict[int, bugzilla.BugDict] | None = None,
    ) -> bool:
        if bug.get("cf_performance_impact") in ("low", "medium", "high"):
            return True

        if any(
            keyword.startswith(prefix)
            for keyword in bug["keywords"]
            for prefix in self.keyword_prefixes
        ):
            return True

        bug_whiteboard = bug["whiteboard"].lower()
        if any(prefix in bug_whiteboard for prefix in self.whiteboard_prefixes):
            return True

        return False


class IsMemoryBug(SingleBugFeature):
    """Determine if the bug is related to memory based on given bug data."""

    name = "Is Memory Bug"
    type_name = "memory"
    keyword_prefixes = ("memory-",)
    whiteboard_prefixes = ("[overhead", "[memshrink")

    def __call__(
        self,
        bug: bugzilla.BugDict,
        bug_map: dict[int, bugzilla.BugDict] | None = None,
    ) -> bool:
        if bug_map is not None:
            for bug_id in bug["blocks"]:
                if bug_id not in bug_map:
                    continue

                alias = bug_map[bug_id]["alias"]
                if alias and alias.startswith("memshrink"):
                    return True

        if any(
            keyword.startswith(prefix)
            for keyword in bug["keywords"]
            for prefix in self.keyword_prefixes
        ):
            return True

        bug_whiteboard = bug["whiteboard"].lower()
        if any(prefix in bug_whiteboard for prefix in self.whiteboard_prefixes):
            return True

        return False


class IsPowerBug(SingleBugFeature):
    """Determine if the bug is related to power based on given bug data."""

    name = "Is Power Bug"
    type_name = "power"
    keyword_prefixes = ("power",)
    whiteboard_prefixes = ("[power",)

    def __call__(
        self,
        bug: bugzilla.BugDict,
        bug_map: dict[int, bugzilla.BugDict] | None = None,
    ) -> bool:
        if any(
            keyword.startswith(prefix)
            for keyword in bug["keywords"]
            for prefix in self.keyword_prefixes
        ):
            return True

        bug_whiteboard = bug["whiteboard"].lower()
        if any(prefix in bug_whiteboard for prefix in self.whiteboard_prefixes):
            return True

        return False


class IsSecurityBug(SingleBugFeature):
    """Determine if the bug is related to security based on given bug data."""

    name = "Is Security Bug"
    type_name = "security"
    keyword_prefixes = ("sec-", "csectype-")
    whiteboard_prefixes = ("[client-bounty-form", "[sec-survey")

    def __call__(
        self,
        bug: bugzilla.BugDict,
        bug_map: dict[int, bugzilla.BugDict] | None = None,
    ) -> bool:
        if any(
            keyword.startswith(prefix)
            for keyword in bug["keywords"]
            for prefix in self.keyword_prefixes
        ):
            return True

        bug_whiteboard = bug["whiteboard"].lower()
        if any(prefix in bug_whiteboard for prefix in self.whiteboard_prefixes):
            return True

        return False


class IsCrashBug(SingleBugFeature):
    """Determine if the bug is related to crash based on given bug data."""

    name = "Is Crash Bug"
    type_name = "crash"
    keyword_prefixes = ("crash", "crashreportid")

    def __call__(
        self,
        bug: bugzilla.BugDict,
        bug_map: dict[int, bugzilla.BugDict] | None = None,
    ) -> bool:
        # Checking for `[@` will exclude some bugs that do not have valid
        # signatures: https://mzl.la/46XAqRF
        if bug.get("cf_crash_signature") and "[@" in bug["cf_crash_signature"]:
            return True

        if any(
            keyword.startswith(prefix)
            for keyword in bug["keywords"]
            for prefix in self.keyword_prefixes
        ):
            return True

        return False


class BugTypes(SingleBugFeature):
    """Determine bug type."""

    name = "Infer Bug Type"
    bug_type_extractors: list = [
        IsCrashBug(),
        IsMemoryBug(),
        IsPerformanceBug(),
        IsPowerBug(),
        IsSecurityBug(),
    ]

    def __call__(
        self,
        bug: bugzilla.BugDict,
        bug_map: dict[int, bugzilla.BugDict] | None = None,
    ) -> list[str]:
        """Infer bug types based on various bug characteristics.

        Args:
        - bug (bugzilla.BugDict): A dictionary containing bug data.
        - bug_map (Optional[dict[int, bugzilla.BugDict]]): A mapping
            of bug IDs to bug dictionaries. Default is None.

        Returns:
        - list[str]: A list of inferred bug types (e.g., "memory", "power",
            "performance", "security", "crash").
        """
        return [
            is_type.type_name
            for is_type in self.bug_type_extractors
            if is_type(bug, bug_map)
        ]


class BugType(SingleBugFeature):
    """Extracts the type of the bug."""

    def __call__(self, bug, **kwargs):
        return bug["type"]
