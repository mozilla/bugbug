"""Install the developer tools `mach bootstrap` offers coding agents.

Non-interactive bootstrap skips them (treeherder-cli, searchfox-cli, ...), so
install them the way it would: from the tree's toolchain artifacts into
~/.cargo/bin. Not needed once bug 2080915 makes non-interactive bootstrap
install them.

Runs with `./mach python`, which has the tree's mozboot and mozbuild; this
directory isn't a package, so nothing imports it by mistake elsewhere.
"""

import shutil
from pathlib import Path

from mozboot.base import BaseBootstrapper
from mozbuild.bootstrap import bootstrap_toolchain

cargo_bin = Path.home() / ".cargo" / "bin"
cargo_bin.mkdir(parents=True, exist_ok=True)
for tool in BaseBootstrapper.CARGO_TOOLS:
    tool_dir = bootstrap_toolchain(tool)
    if tool_dir:
        shutil.copy2(Path(tool_dir) / tool, cargo_bin / tool)
    else:
        print(f"Could not install {tool}.")
