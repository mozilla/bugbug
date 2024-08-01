# Downloading Data Using BugBug

BugBug relies on various types of data, such as bugs, commits, issues, and crash reports, to build its models. Although all this data is publicly available through different APIs, retrieving it every time we train a model is not an efficient solution. Hence, a copy of the data is saved as downloadable compressed files through a simple API.

> **Note:**
> You can use the data outside this project by using BugBug as a dependency (`pip install bugbug`).

## Bugzilla Bugs

```py
from bugbug import bugzilla, db

# Downland the latest version if the data set if it is not already downloaded
db.download(bugzilla.BUGS_DB)

# Iterate over all bugs in the dataset
for bug in bugzilla.get_bugs():
    # This is the same as if you retrieved the bug through Bugzilla REST API:
    # https://bmo.readthedocs.io/en/latest/api/core/v1/bug.html
    print(bug["id"])
```

## Phabricator Revisions

```py
from bugbug import phabricator, db

db.download(bugzilla.REVISIONS_DB)

for revision in phabricator.get_revisions():
    # The revision here combines the results retrieved from two API endpoints:
    # https://phabricator.services.mozilla.com/conduit/method/differential.revision.search/
    # https://phabricator.services.mozilla.com/conduit/method/transaction.search/
    print(revision["id"])
```

## Repository Commits

```py
from bugbug import repository, db

db.download(bugzilla.COMMITS_DB)

for commit in repository.get_commits():
    print(commit["node"])
```

## Github Issues

> _TODO_

## Mozilla Crash Reports

> _TODO_
