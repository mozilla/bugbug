# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

"""Check candidate whitespace, formatting and lint before spending try resources."""

import subprocess
import sys
import tempfile
from pathlib import Path

from agent_tools.firefox_env import toolchain_environment

from .evidence import EvidenceError

# Bootstrap only the revision's formatting tools. The historical lint setup calls
# full configure first, which unnecessarily requires Rust and a Firefox build SDK.
CLANG_BOOTSTRAP = [
    "./mach",
    "python",
    "-c",
    "from pathlib import Path; "
    "from mozbuild.bootstrap import bootstrap_toolchain; "
    "toolchain = bootstrap_toolchain('clang-tools/clang-tidy'); "
    "assert toolchain and (Path(toolchain) / 'bin/clang-format').is_file(), "
    "'clang-format bootstrap failed'",
]


def check_candidate(repo: Path, base: str, log: Path) -> None:
    files = (
        subprocess.check_output(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", "-z", base, "HEAD"],
            cwd=repo,
        )
        .decode()
        .split("\0")
    )
    files = [p for p in files if p]
    run_lint(repo, files, log, whitespace=["git", "diff", "--check", base, "HEAD"])


def run_lint(
    repo: Path,
    files: list[str],
    log: Path,
    *,
    lint_args: list[str] | None = None,
    whitespace: list[str] | None = None,
    echo: bool = False,
) -> None:
    """Use the same environment and setup-failure checks for both callers."""
    args = lint_args if lint_args is not None else ["--", *files]
    commands = [whitespace] if whitespace else []
    if files or lint_args is not None:
        commands += [
            CLANG_BOOTSTRAP,
            ["./mach", "lint", "--setup", *[arg for arg in args if arg != "--fix"]],
            ["./mach", "lint", *args],
        ]
        if lint_args is None:
            commands.append(["./mach", "lint", "-l", "clang-format", "--", *files])
    env = toolchain_environment()
    with log.open("w+") as output:
        for command in commands:
            start = output.tell()
            output.write(f"Running: {command!r}\n")
            output.flush()
            if echo:
                print(f"Running: {command!r}", flush=True)
            try:
                result = subprocess.run(
                    command,
                    cwd=repo,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=1200,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                output.write(f"Check unavailable: {exc}\n")
                raise EvidenceError(
                    "Local lint/format check could not complete; see preflight log"
                ) from exc
            output.flush()
            # Some mach versions print setup failures but still exit zero.
            output.seek(start)
            logged = output.read()
            if echo:
                print(logged.split("\n", 1)[-1], end="", flush=True)
            logged = logged.lower()
            skipped = (
                "problem with lint setup" in logged or "a failure occurred in" in logged
            )
            if result.returncode or skipped:
                raise EvidenceError(
                    "Local lint/format check failed; see preflight log (no try push)"
                )


def main() -> None:
    """Run the same lint setup for the model's checks and automatic formatting."""
    args = sys.argv[1:]
    if "--help" in args or "-h" in args:
        raise SystemExit(subprocess.call(["./mach", "lint", "--help"]))
    # mach retains ownership of option parsing; paths after -- are unambiguous.
    files = args[args.index("--") + 1 :] if "--" in args else args
    with tempfile.TemporaryDirectory(prefix="perf-preflight-") as directory:
        try:
            run_lint(
                Path.cwd(),
                files,
                Path(directory) / "lint.log",
                lint_args=args,
                echo=True,
            )
        except EvidenceError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
