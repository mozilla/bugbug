# -*- coding: utf-8 -*-
import os

ALLOW_MISSING_MODELS = bool(int(os.environ.get("BUGBUG_ALLOW_MISSING_MODELS", "0")))
