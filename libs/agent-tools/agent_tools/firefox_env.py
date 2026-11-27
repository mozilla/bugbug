# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Environment and commands for Firefox tools, without optional build imports."""

import os
from collections.abc import Mapping
from pathlib import Path


def toolchain_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Find tools installed by mach bootstrap or cargo in every subprocess."""
    env = dict(os.environ if environ is None else environ)
    home = Path(env.get("HOME") or Path.home())
    cargo_home = Path(env.get("CARGO_HOME") or home / ".cargo")
    state = Path(env.get("MOZBUILD_STATE_PATH") or home / ".mozbuild")
    bins = [cargo_home / "bin", state / "clang" / "bin"]
    env["PATH"] = os.pathsep.join([*(str(p) for p in bins), env.get("PATH", "")])
    return env
