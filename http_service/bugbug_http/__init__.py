# -*- coding: utf-8 -*-
import os
import tempfile

ALLOW_MISSING_MODELS = bool(int(os.environ.get("BUGBUG_ALLOW_MISSING_MODELS", "0")))

REPO_DIR = os.environ.get(
    "BUGBUG_REPO_DIR", os.path.join(tempfile.gettempdir(), "bugbug-hg")
)

# A git repository in which the commits of git try pushes are fetched.
GIT_REPO_DIR = os.environ.get(
    "BUGBUG_GIT_REPO_DIR", os.path.join(tempfile.gettempdir(), "bugbug-git")
)

# The git try repositories (as Treeherder projects), with their GitHub repository.
GIT_TRY_REPOSITORIES = {"firefox-dev": "mozilla-firefox/firefox-dev"}
