# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Gunicorn configuration for the web processes (loaded with ``-c python:bugbug_http.gunicorn_conf``)."""

import gc

# With --preload, the workers share the master's memory (the imported modules) until they write to
# it, and a garbage collection writes to every object it traverses. Following
# https://docs.python.org/3/library/gc.html#gc.freeze, collections are disabled while the app is
# preloaded (this module is loaded before it), so that they don't free objects and leave holes
# that the workers' allocations would fill, writing to the shared pages.
gc.disable()


def pre_fork(server, worker):
    # Move the objects allocated so far to the permanent generation, which collections don't
    # traverse, then enable the collections again: in the master, which keeps running (e.g. Sentry's
    # threads), and in the worker, which inherits them.
    gc.freeze()
    gc.enable()
