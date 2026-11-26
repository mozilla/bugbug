# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""The names of the job functions of bugbug_http.models, which the RQ workers run.

The web processes refer to the job functions with these names rather than importing
bugbug_http.models, as it loads the ML libraries. Imported by name, a misspelled one fails like a
misspelled function would: as an undefined name for the linter, or an ImportError.
"""

classify_broken_site_report = "classify_broken_site_report"
classify_bug = "classify_bug"
classify_comment = "classify_comment"
classify_commits = "classify_commits"
classify_issue = "classify_issue"
classify_perf_regression = "classify_perf_regression"
get_bug_commits = "get_bug_commits"
get_config_specific_groups = "get_config_specific_groups"
schedule_tests = "schedule_tests"
schedule_tests_from_patch = "schedule_tests_from_patch"
