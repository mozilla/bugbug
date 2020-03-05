# -*- coding: utf-8 -*-
import os
import tempfile

ALLOW_MISSING_MODELS = bool(int(os.environ.get("BUGBUG_ALLOW_MISSING_MODELS", "0")))

REPO_DIR = os.environ.get(
    "BUGBUG_REPO_DIR", os.path.join(tempfile.gettempdir(), "bugbug-hg")
)
