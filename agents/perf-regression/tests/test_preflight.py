# Copyright 2026 Mozilla Foundation
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

import subprocess

import pytest
from hackbot_agents.perf_regression.evidence import EvidenceError
from hackbot_agents.perf_regression.preflight import (
    CLANG_BOOTSTRAP,
    check_candidate,
)


@pytest.mark.parametrize("failure", [None, "lint", "bootstrap", "timeout", "skipped"])
def test_preflight_checks_changed_files_and_blocks_failure(tmp_path, failure):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    def git(*args):
        return (
            subprocess.check_output(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.com",
                    *args,
                ],
                cwd=tmp_path,
            )
            .decode()
            .strip()
        )

    (tmp_path / "base").write_text("base\n")
    manifest = tmp_path / "python/sites/lint.txt"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("pypi:rumdl==0.1.91\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    # Fake mach records exact arguments and can report a lint failure.
    mach = tmp_path / "mach"
    mach.write_text(
        '#!/bin/sh\n[ "$(cat python/sites/lint.txt)" = "pypi:rumdl==0.1.91" ] || exit 9\nprintf "%s\\n" "$@" >> calls\n'
        + (
            f'[ "$1" = "{"python" if failure == "bootstrap" else "lint"}" ] && exit 1\n'
            if failure in {"lint", "bootstrap"}
            else ""
        )
        + (
            'echo "ERROR: problem with lint setup, skipping cargo-audit"\n'
            if failure == "skipped"
            else ""
        )
        + "exit 0\n"
    )
    mach.chmod(0o755)
    (tmp_path / "file with spaces.cpp").write_text("int x;\n")
    git("add", "file with spaces.cpp")
    git("commit", "-qm", "candidate")
    log = tmp_path / "preflight.log"
    if failure == "timeout":
        from unittest.mock import patch

        real_run = subprocess.run

        def timeout_run(*args, **kwargs):
            if kwargs.get("timeout") == 1200:
                raise subprocess.TimeoutExpired("mach", 1200)
            return real_run(*args, **kwargs)

        with (
            patch(
                "hackbot_agents.perf_regression.preflight.subprocess.run",
                side_effect=timeout_run,
            ),
            pytest.raises(EvidenceError, match="could not complete"),
        ):
            check_candidate(tmp_path, base, log)
    elif failure:
        with pytest.raises(EvidenceError, match="no try push"):
            check_candidate(tmp_path, base, log)
    else:
        check_candidate(tmp_path, base, log)
        assert (tmp_path / "calls").read_text().splitlines() == [
            *CLANG_BOOTSTRAP[1:],
            "lint",
            "--setup",
            "--",
            "file with spaces.cpp",
            "lint",
            "--",
            "file with spaces.cpp",
            "lint",
            "-l",
            "clang-format",
            "--",
            "file with spaces.cpp",
        ]
    assert log.exists()
    assert manifest.read_text() == "pypi:rumdl==0.1.91\n"
    if failure == "bootstrap":
        assert (tmp_path / "calls").read_text().splitlines() == CLANG_BOOTSTRAP[1:]


def test_model_wrapper_blocks_skipped_setup_like_orchestrator(tmp_path, monkeypatch):
    from hackbot_agents.perf_regression import preflight

    mach = tmp_path / "mach"
    mach.write_text(
        "#!/bin/sh\necho 'ERROR: problem with lint setup, skipping cargo-audit'\nexit 0\n"
    )
    mach.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(preflight.sys, "argv", ["preflight", "--", "file.cpp"])
    with pytest.raises(SystemExit) as exc:
        preflight.main()
    assert exc.value.code == 1
